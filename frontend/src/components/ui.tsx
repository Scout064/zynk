import type { ReactNode } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-zinc-800 bg-zinc-900/60 shadow-lg ${className}`}>
      {children}
    </div>
  );
}

export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  type = "button",
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  const styles = {
    default: "bg-zinc-800 hover:bg-zinc-700 text-zinc-100 border border-zinc-700",
    primary: "bg-indigo-600 hover:bg-indigo-500 text-white",
    danger: "bg-red-600/90 hover:bg-red-500 text-white",
    ghost: "bg-transparent hover:bg-zinc-800 text-zinc-300",
  }[variant];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

export function Input({
  value,
  onChange,
  placeholder,
  type = "text",
  required,
  min,
  autoComplete,
  className = "",
}: {
  value: string | number;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  required?: boolean;
  min?: number;
  autoComplete?: string;
  className?: string;
}) {
  return (
    <input
      type={type}
      value={value}
      required={required}
      min={min}
      placeholder={placeholder}
      autoComplete={autoComplete}
      onChange={(e) => onChange(e.target.value)}
      className={`w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-100 placeholder-zinc-500 outline-none focus:border-indigo-500 ${className}`}
    />
  );
}

export function Select({
  value,
  onChange,
  options,
  className = "",
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  className?: string;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-100 outline-none focus:border-indigo-500 ${className}`}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "green" | "red" | "amber" | "indigo";
}) {
  const tones = {
    neutral: "bg-zinc-800 text-zinc-300 border-zinc-700",
    green: "bg-emerald-950 text-emerald-400 border-emerald-800",
    red: "bg-red-950 text-red-400 border-red-800",
    amber: "bg-amber-950 text-amber-400 border-amber-800",
    indigo: "bg-indigo-950 text-indigo-300 border-indigo-800",
  }[tone];
  return (
    <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${tones}`}>
      {children}
    </span>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
}) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-zinc-300">
      <span
        onClick={() => onChange(!checked)}
        className={`relative h-5 w-9 rounded-full transition-colors ${checked ? "bg-indigo-600" : "bg-zinc-700"}`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${checked ? "left-[18px]" : "left-0.5"}`}
        />
      </span>
      {label}
    </label>
  );
}

export function Spinner() {
  return (
    <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-zinc-600 border-t-indigo-400" />
  );
}

export function StatusDot({ reachable }: { reachable: boolean | null }) {
  const cls =
    reachable === null
      ? "bg-zinc-600"
      : reachable
        ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.7)]"
        : "bg-red-500 shadow-[0_0_6px_rgba(239,68,68,0.7)]";
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${cls}`} />;
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
      <p className="text-zinc-300">{title}</p>
      {hint && <p className="text-sm text-zinc-500">{hint}</p>}
    </div>
  );
}
