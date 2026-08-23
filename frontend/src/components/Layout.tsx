import { useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { setToken } from "../api/client";

const NAV = [
  { to: "/", label: "Dashboard", icon: "▦" },
  { to: "/devices", label: "Devices", icon: "⛁" },
  { to: "/schedules", label: "Schedules", icon: "⏱" },
  { to: "/audit", label: "Audit Log", icon: "☰" },
];

export function Layout({ children, username }: { children: ReactNode; username: string }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  return (
    <div className="flex min-h-screen">
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-56 flex-col border-r border-zinc-800 bg-zinc-950/95 backdrop-blur transition-transform md:static md:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="flex h-14 items-center gap-2 border-b border-zinc-800 px-4">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-600 text-sm font-bold text-white">
            Z
          </span>
          <span className="text-lg font-semibold tracking-tight">Zynk</span>
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-indigo-600/15 text-indigo-300"
                    : "text-zinc-400 hover:bg-zinc-800/70 hover:text-zinc-200"
                }`
              }
            >
              <span className="w-4 text-center opacity-70">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-zinc-800 p-3">
          <div className="flex items-center justify-between px-2">
            <span className="text-sm text-zinc-400">@{username}</span>
            <button
              onClick={() => {
                setToken(null);
                navigate("/login");
              }}
              className="text-xs text-zinc-500 hover:text-red-400"
            >
              Sign out
            </button>
          </div>
        </div>
      </aside>
      {open && (
        <div className="fixed inset-0 z-30 bg-black/50 md:hidden" onClick={() => setOpen(false)} />
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center gap-3 border-b border-zinc-800 px-4 md:hidden">
          <button onClick={() => setOpen(true)} className="text-zinc-300">
            ☰
          </button>
          <span className="font-semibold">Zynk</span>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 p-4 md:p-8">{children}</main>
      </div>
    </div>
  );
}

export function PageHeader({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <div className="flex items-center gap-2">{actions}</div>
    </div>
  );
}
