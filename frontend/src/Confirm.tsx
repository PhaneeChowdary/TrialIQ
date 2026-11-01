import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { AlertTriangle, ShieldAlert, Sparkles } from "lucide-react";

/* Confirmation dialogs for costly or risky actions .
   - Cancel has the focus, so an accidental Enter does nothing.
   - Escape and a click outside cancel.
   - `typeToConfirm`: the person must type a word before the action button works. */

export type Tone = "primary" | "warning" | "danger";

export interface ConfirmOptions {
  title: string;
  message: React.ReactNode;
  details?: React.ReactNode[];
  confirmLabel: string;
  tone?: Tone;
  typeToConfirm?: string;
}

type Pending = ConfirmOptions & { resolve: (ok: boolean) => void };

const ConfirmContext = createContext<(o: ConfirmOptions) => Promise<boolean>>(async () => false);

export function useConfirm() {
  return useContext(ConfirmContext);
}

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const confirm = useCallback((o: ConfirmOptions) => new Promise<boolean>((resolve) => setPending({ ...o, resolve })), []);
  const close = (ok: boolean) => { pending?.resolve(ok); setPending(null); };
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && <ConfirmDialog options={pending} onClose={close} />}
    </ConfirmContext.Provider>
  );
}

function ConfirmDialog({ options, onClose }: { options: ConfirmOptions; onClose: (ok: boolean) => void }) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const [typed, setTyped] = useState("");
  const tone = options.tone || "primary";
  const ready = !options.typeToConfirm || typed.trim().toUpperCase() === options.typeToConfirm.toUpperCase();
  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const Icon = tone === "danger" ? ShieldAlert : tone === "warning" ? AlertTriangle : Sparkles;
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => onClose(false)}>
      <section className={`modal ${tone}`} role="alertdialog" aria-modal="true" aria-label={options.title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head"><Icon /><span>{options.title}</span></div>
        <div className="modal-body">{options.message}</div>
        {options.details && options.details.length > 0 && (
          <ul className="modal-details">{options.details.map((d, i) => <li key={i}>{d}</li>)}</ul>
        )}
        {options.typeToConfirm && (
          <label className="modal-type">
            <span>Type <b>{options.typeToConfirm}</b> to confirm</span>
            <input autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && ready && onClose(true)} />
          </label>
        )}
        <div className="modal-actions">
          <button ref={cancelRef} className="btn" onClick={() => onClose(false)}>Cancel</button>
          <button className={`btn ${tone === "danger" ? "danger-solid" : tone === "warning" ? "warning-solid" : "primary"}`}
            disabled={!ready} onClick={() => onClose(true)}>{options.confirmLabel}</button>
        </div>
      </section>
    </div>
  );
}
