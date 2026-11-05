import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { useAutosave } from "./useAutosave";

const KEY = "test.draft";
interface Doc { t: string }
const apiError = (status: number, details?: unknown) => Object.assign(new Error("refus"), { status, details });

function setup(save: (v: Doc, rev: number) => Promise<{ revision: number }>, opts: { paused?: boolean } = {}) {
  const onRecover = vi.fn();
  const hook = renderHook(() => {
    const [value, setValue] = useState<Doc>({ t: "a" });
    const [baseline, setBaseline] = useState<{ value: Doc; revision: number } | null>({ value: { t: "a" }, revision: 1 });
    const auto = useAutosave<Doc>({ storageKey: KEY, value, baseline, save, onRecover, paused: opts.paused });
    return { auto, setValue, setBaseline };
  });
  return { hook, onRecover };
}
const edit = (h: ReturnType<typeof setup>["hook"], t: string) => act(() => { h.result.current.setValue({ t }); });
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

beforeEach(() => { vi.useFakeTimers(); localStorage.clear(); });
afterEach(() => { vi.useRealTimers(); });

describe("useAutosave", () => {
  it("saves once after the delay, with the revision it was based on, and mirrors the content locally meanwhile", async () => {
    const save = vi.fn().mockResolvedValue({ revision: 2 });
    const { hook } = setup(save);
    expect(hook.result.current.auto.state).toBe("saved");
    edit(hook, "b"); edit(hook, "bc");
    expect(hook.result.current.auto.state).toBe("dirty");
    expect(JSON.parse(localStorage.getItem(KEY) as string)).toEqual({ t: "bc" });
    await tick(1300);
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith({ t: "bc" }, 1);
    expect(hook.result.current.auto.state).toBe("saved");
    expect(hook.result.current.auto.savedAt).toBeInstanceOf(Date);
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it("never runs two saves at once: an edit during a save goes right after it, on the revision the server just returned", async () => {
    let release: (v: { revision: number }) => void = () => undefined;
    const save = vi.fn()
      .mockImplementationOnce(() => new Promise((r) => { release = r; }))
      .mockResolvedValue({ revision: 3 });
    const { hook } = setup(save);
    edit(hook, "b");
    await tick(1300);
    expect(hook.result.current.auto.state).toBe("saving");
    edit(hook, "c"); // while the first request is still running
    await tick(1300);
    expect(save).toHaveBeenCalledTimes(1); // still waiting for the first answer
    await act(async () => { release({ revision: 2 }); await vi.advanceTimersByTimeAsync(10); });
    expect(save).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenLastCalledWith({ t: "c" }, 2);
    expect(hook.result.current.auto.state).toBe("saved");
  });

  it("stops on a conflict and waits for a decision; overwriting retries on the server's current revision", async () => {
    const save = vi.fn().mockRejectedValueOnce(apiError(409, { current_revision: 7 })).mockResolvedValue({ revision: 8 });
    const { hook } = setup(save);
    edit(hook, "b");
    await tick(1300);
    expect(hook.result.current.auto.state).toBe("conflict");
    edit(hook, "bc");
    await tick(5000);
    expect(save).toHaveBeenCalledTimes(1); // nothing more is sent by itself
    await act(async () => { await hook.result.current.auto.overwrite(); });
    expect(save).toHaveBeenLastCalledWith({ t: "bc" }, 7);
    expect(hook.result.current.auto.state).toBe("saved");
  });

  it("shows « invalid » on a 400, keeps the copy locally, and tries again at the next edit", async () => {
    const save = vi.fn().mockRejectedValueOnce(apiError(400)).mockResolvedValue({ revision: 2 });
    const { hook } = setup(save);
    edit(hook, "b");
    await tick(1300);
    expect(hook.result.current.auto.state).toBe("invalid");
    expect(localStorage.getItem(KEY)).not.toBeNull();
    await tick(10_000);
    expect(save).toHaveBeenCalledTimes(1); // no retry loop on a refused draft
    edit(hook, "bc");
    await tick(1300);
    expect(save).toHaveBeenCalledTimes(2);
    expect(hook.result.current.auto.state).toBe("saved");
  });

  it("retries by itself after a network failure", async () => {
    const save = vi.fn().mockRejectedValueOnce(apiError(0)).mockResolvedValue({ revision: 2 });
    const { hook } = setup(save);
    edit(hook, "b");
    await tick(1300);
    expect(hook.result.current.auto.state).toBe("error");
    await tick(5100);
    expect(save).toHaveBeenCalledTimes(2);
    expect(hook.result.current.auto.state).toBe("saved");
  });

  it("flush saves right away and says whether the server holds the content (false on a conflict)", async () => {
    const save = vi.fn().mockResolvedValueOnce({ revision: 2 }).mockRejectedValueOnce(apiError(409, { current_revision: 5 }));
    const { hook } = setup(save);
    edit(hook, "b");
    let ok = false;
    await act(async () => { ok = await hook.result.current.auto.flush(); });
    expect(ok).toBe(true);
    expect(save).toHaveBeenCalledTimes(1);
    edit(hook, "c");
    await act(async () => { ok = await hook.result.current.auto.flush(); });
    expect(ok).toBe(false);
    await act(async () => { ok = await hook.result.current.auto.flush(); });
    expect(ok).toBe(false); // the conflict stays until the person chooses
  });

  it("offers back an unsaved local copy found at load, and forgets it when ignored", () => {
    localStorage.setItem(KEY, JSON.stringify({ t: "perdu" }));
    const { hook, onRecover } = setup(vi.fn());
    expect(hook.result.current.auto.recover).toEqual({ t: "perdu" });
    act(() => hook.result.current.auto.acceptRecovery());
    expect(onRecover).toHaveBeenCalledWith({ t: "perdu" });
    expect(hook.result.current.auto.recover).toBeNull();
    localStorage.setItem(KEY, JSON.stringify({ t: "autre" }));
    act(() => { hook.result.current.setBaseline({ value: { t: "a" }, revision: 1 }); });
    expect(hook.result.current.auto.recover).toEqual({ t: "autre" });
    act(() => hook.result.current.auto.discardRecovery());
    expect(hook.result.current.auto.recover).toBeNull();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it("does not offer a local copy identical to the server's", () => {
    localStorage.setItem(KEY, JSON.stringify({ t: "a" }));
    const { hook } = setup(vi.fn());
    expect(hook.result.current.auto.recover).toBeNull();
  });

  it("asks before the tab closes while something is unsaved, not otherwise", () => {
    const { hook } = setup(vi.fn().mockResolvedValue({ revision: 2 }));
    const ask = () => { const e = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; };
    expect(ask()).toBe(false);
    edit(hook, "b");
    expect(ask()).toBe(true);
  });

  it("schedules nothing while paused (publishing), and a flush still works", async () => {
    const save = vi.fn().mockResolvedValue({ revision: 2 });
    const { hook } = setup(save, { paused: true });
    edit(hook, "b");
    await tick(5000);
    expect(save).not.toHaveBeenCalled();
    await act(async () => { await hook.result.current.auto.flush(); });
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("sends a last save when the person leaves the page with something pending", async () => {
    const save = vi.fn().mockResolvedValue({ revision: 2 });
    const { hook } = setup(save);
    edit(hook, "b");
    hook.unmount();
    await tick(10);
    expect(save).toHaveBeenCalledWith({ t: "b" }, 1);
  });
});
