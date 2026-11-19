from django.http import JsonResponse
from django.urls import path

urlpatterns = [path("ping/", lambda request: JsonResponse({"pong": True}))]
