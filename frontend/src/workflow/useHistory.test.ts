import { act, renderHook } from "@testing-library/react";
import { useHistory } from "./useHistory";

describe("useHistory", () => {
  it("undoes, redoes and drops the redo branch after a new change", () => {
    const { result } = renderHook(() => useHistory(0));
    act(() => result.current.set(1));
    act(() => result.current.set(2));
    expect(result.current.value).toBe(2);
    act(() => result.current.undo());
    expect(result.current.value).toBe(1);
    expect(result.current.canRedo).toBe(true);
    act(() => result.current.redo());
    expect(result.current.value).toBe(2);
    act(() => result.current.undo());
    act(() => result.current.set(9));
    expect(result.current.canRedo).toBe(false);
    act(() => result.current.undo());
    expect(result.current.value).toBe(1);
  });

  it("replace() resets history and identical values do not create history entries", () => {
    const { result } = renderHook(() => useHistory({ a: 1 }));
    const same = result.current.value;
    act(() => result.current.set(same));
    expect(result.current.canUndo).toBe(false);
    act(() => result.current.set({ a: 2 }));
    act(() => result.current.replace({ a: 5 }));
    expect(result.current.value).toEqual({ a: 5 });
    expect(result.current.canUndo).toBe(false);
  });
});
