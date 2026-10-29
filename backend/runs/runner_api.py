"""HTTP protocol of runner agents (see docs/RUNNERS.md). The runner pulls jobs; the platform never connects to it.

GET|POST /api/v1/runner/me/                    configuration (POST: the agent reports its version/platform/runtimes)
POST     /api/v1/runner/jobs/request/          long-poll for the next job it may run (204 when none)
POST     /api/v1/runner/jobs/<id>/heartbeat/   extend the lease; answers whether to cancel (409 when fenced out)
POST     /api/v1/runner/jobs/<id>/complete/    hand the outcome back (masked, validated and recorded by the platform)
"""

import json
import time

from django.conf import settings
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from vault.injection import InjectionError, resolve_env

from . import services
from .finalize import finalize_attempt
from .models import Execution, Runner
from .runner_auth import IsRunner, RunnerAuthentication, RunnerThrottle
from .runners import RunOutcome


class RunnerView(APIView):
    authentication_classes = [RunnerAuthentication]
    permission_classes = [IsRunner]
    throttle_classes = [RunnerThrottle]


def _config(runner: Runner) -> dict:
    return {
        "id": str(runner.id),
        "name": runner.name,
        "scope": runner.scope,
        "tags": runner.tags,
        "runtimes": runner.runtimes,
        "max_concurrent": runner.max_concurrent,
        "paused": runner.paused,
        "lease_seconds": settings.ORCH_LEASE_SECONDS,
        "heartbeat_seconds": max(2, settings.ORCH_LEASE_SECONDS // 3),
        "long_poll_seconds": settings.ORCH_RUNNER_LONG_POLL_SECONDS,
    }


class MeIn(serializers.Serializer):
    version = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    platform = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")


class RunnerMeView(RunnerView):
    def get(self, request):
        return Response(_config(request.auth))

    def post(self, request):
        ser = MeIn(data=request.data)
        ser.is_valid(raise_exception=True)
        Runner.objects.filter(pk=request.auth.pk).update(
            agent_version=ser.validated_data["version"], platform=ser.validated_data["platform"]
        )
        return Response(_config(Runner.objects.get(pk=request.auth.pk)))


def _spec(execution: Execution, attempt: int, env: dict) -> dict:
    v = execution.action_version
    limits = {**v.limits, **(execution.limits or {})}  # per-execution override (script tasks); versions stay immutable
    return {
        "job_id": str(execution.id),
        "attempt": attempt,
        "workspace": execution.workspace.slug,
        "runtime": v.runtime,
        "entrypoint": v.entrypoint,
        "source": v.source,
        "network_policy": v.network_policy,
        "limits": limits,
        "timeout_seconds": int(limits.get("timeout_seconds", 30)),
        "inputs": execution.inputs,
        "env": env,
        "lease_seconds": settings.ORCH_LEASE_SECONDS,
    }


def _claim_one(runner: Runner) -> dict | None:
    while True:
        claimed = services.claim_next(f"runner:{runner.name}", runner)
        if claimed is None:
            return None
        execution, attempt = claimed
        try:
            env, _ = resolve_env(execution, execution.action_version)  # decrypted here, handed to THIS job only
        except InjectionError as exc:
            services.complete_attempt(
                execution.id,
                attempt,
                ok=False,
                error={
                    "code": exc.code,
                    "message": exc.message,
                    "next_step": "Vérifiez la connexion dans « Connexions ».",
                },
            )
            continue  # that job is over; look for the next one
        return _spec(execution, attempt, env)


class RequestIn(serializers.Serializer):
    wait = serializers.IntegerField(min_value=0, max_value=300, required=False, default=0)


class JobRequestView(RunnerView):
    def post(self, request):
        ser = RequestIn(data=request.data)
        ser.is_valid(raise_exception=True)
        deadline = time.monotonic() + min(ser.validated_data["wait"], settings.ORCH_RUNNER_LONG_POLL_SECONDS)
        runner = request.auth
        while True:
            runner = Runner.objects.filter(
                pk=runner.pk
            ).first()  # pause / token rotation / deletion apply to a waiting call
            if runner is None:
                return Response(
                    {"error": {"code": "runner_revoked", "message": "Runner supprimé.", "details": None}}, status=401
                )
            job = None if runner.paused else _claim_one(runner)
            if job is not None:
                return Response(job)
            if time.monotonic() >= deadline:
                return Response(status=204)
            time.sleep(1.0)


def _job(runner: Runner, pk) -> Execution:
    """A runner only ever sees its own jobs (same answer for 'unknown' and 'someone else's')."""
    ex = Execution.objects.select_related("action_version", "workspace").filter(pk=pk, runner=runner).first()
    if ex is None:
        raise NotFound()
    return ex


class HeartbeatIn(serializers.Serializer):
    attempt = serializers.IntegerField(min_value=1)


class JobHeartbeatView(RunnerView):
    def post(self, request, pk):
        ser = HeartbeatIn(data=request.data)
        ser.is_valid(raise_exception=True)
        ex = _job(request.auth, pk)
        if not services.heartbeat(ex.id, ser.validated_data["attempt"]):
            return Response(
                {
                    "error": {
                        "code": "stale",
                        "message": "Cette tentative n'est plus la tentative en cours.",
                        "details": None,
                    }
                },
                status=409,
            )
        return Response({"cancel": services.cancel_requested(ex.id)})


class CompleteIn(serializers.Serializer):
    attempt = serializers.IntegerField(min_value=1)
    ok = serializers.BooleanField()
    result = serializers.JSONField(required=False, allow_null=True, default=None)
    error = serializers.JSONField(required=False, allow_null=True, default=None)
    logs = serializers.CharField(required=False, allow_blank=True, default="", trim_whitespace=False)
    exit_code = serializers.IntegerField(required=False, allow_null=True, default=None)
    cancelled = serializers.BooleanField(required=False, default=False)


def _clean_error(error) -> dict | None:
    if not isinstance(error, dict):
        return None
    return {k: str(error[k])[:300] for k in ("code", "message", "next_step") if k in error}


class JobCompleteView(RunnerView):
    def post(self, request, pk):
        ser = CompleteIn(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        ex = _job(request.auth, pk)
        logs = d["logs"][-settings.ORCH_MAX_LOG_BYTES :]
        outcome = RunOutcome(
            d["ok"],
            result=d["result"],
            error=_clean_error(d["error"]),
            logs=logs,
            exit_code=d["exit_code"],
            cancelled=d["cancelled"],
        )
        if outcome.ok and len(json.dumps(outcome.result)) > settings.ORCH_MAX_RESULT_BYTES:
            outcome = RunOutcome(
                False,
                error={"code": "RESULT_TOO_LARGE", "message": "Le résultat dépasse la taille autorisée."},
                logs=logs,
                exit_code=d["exit_code"],
            )
        try:
            _, to_mask = resolve_env(ex, ex.action_version, audit=False)  # the platform masks, not the runner
        except InjectionError:
            to_mask = []
        if not finalize_attempt(ex, d["attempt"], outcome, to_mask):
            return Response(
                {"error": {"code": "stale", "message": "Résultat tardif ignoré.", "details": None}}, status=409
            )
        return Response({"recorded": True})
