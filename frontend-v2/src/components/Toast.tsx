import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import { Icon } from './Icon';

type ToastContextValue = { flash: (msg: string) => void };
const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState('');
  const timer = useRef<number | null>(null);

  const flash = useCallback((next: string) => {
    setMsg(next);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMsg(''), 2400);
  }, []);

  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  return (
    <ToastContext.Provider value={{ flash }}>
      {children}
      {msg && (
        <div className="toast">
          <Icon name="check" size={16} />
          <span>{msg}</span>
        </div>
      )}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>');
  return ctx;
}
