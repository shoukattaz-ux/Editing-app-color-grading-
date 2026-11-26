import { useSyncExternalStore } from "react";
/** which coloured-light box is selected (shared by the preview overlay and the Properties panel) */
let sel = 0; const subs = new Set<() => void>();
export const setBoxSel = (i: number) => { if (i !== sel) { sel = i; subs.forEach(f => f()); } };
export const useBoxSel = (): number => useSyncExternalStore(f => { subs.add(f); return () => { subs.delete(f); }; }, () => sel);
