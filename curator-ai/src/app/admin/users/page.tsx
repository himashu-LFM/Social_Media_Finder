"use client";

import { useCallback, useEffect, useState } from "react";
import { AppMain } from "@/components/AppMain";
import { AppMobileNav } from "@/components/AppMobileNav";
import { AppPageHeader } from "@/components/AppPageHeader";
import { AppSidebar } from "@/components/AppSidebar";
import { useToast } from "@/components/ToastProvider";
import {
  adminCreateUser,
  adminListUsers,
  adminResetPassword,
  adminSetActive,
  fetchMe,
  type AdminUser,
} from "@/lib/auth";

/**
 * Create and manage accounts.
 *
 * This page is the *only* way an account comes into existence — there is no
 * public sign-up anywhere in the product. Hiding it from non-admins is a
 * courtesy; the API enforces the role, so a curl with an analyst's token gets
 * a 403 regardless of what the UI shows.
 *
 * A newly issued temporary password is shown exactly once, because that is all
 * the server will ever hand back: it is stored only as an Argon2 hash. Losing
 * it means issuing a new one, which is a button, not a problem.
 */
export default function AdminUsersPage() {
  const { pushToast } = useToast();

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);

  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("analyst");
  const [creating, setCreating] = useState(false);
  const [issued, setIssued] = useState<{ email: string; password: string; emailed: boolean } | null>(
    null,
  );

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setUsers(await adminListUsers());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load users.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const me = await fetchMe();
      if (!alive) return;
      // `me` is null when auth is switched off entirely (local dev) — treat
      // that as permitted, exactly as the server does.
      setIsAdmin(me === null || me.role?.toLowerCase() === "admin");
      await reload();
    })();
    return () => {
      alive = false;
    };
  }, [reload]);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const result = await adminCreateUser({ email, name, role });
      setIssued({
        email: result.user.email,
        password: result.temp_password,
        emailed: result.emailed,
      });
      setEmail("");
      setName("");
      setRole("analyst");
      pushToast("Account created.", "success");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the account.");
      pushToast("Could not create the account.", "error");
    } finally {
      setCreating(false);
    }
  }

  async function onResetPassword(user: AdminUser) {
    try {
      const password = await adminResetPassword(user.id);
      setIssued({ email: user.email, password, emailed: false });
      pushToast(`New temporary password issued for ${user.email}.`, "success");
      await reload();
    } catch (err) {
      pushToast(err instanceof Error ? err.message : "Could not reset.", "error");
    }
  }

  async function onToggleActive(user: AdminUser) {
    try {
      await adminSetActive(user.id, !user.is_active);
      await reload();
    } catch (err) {
      pushToast(err instanceof Error ? err.message : "Could not update.", "error");
    }
  }

  if (isAdmin === false) {
    return (
      <Shell>
        <div className="lf-card p-6 text-sm text-slate-400">
          <span className="material-symbols-outlined mb-2 block text-2xl text-amber-400">
            lock
          </span>
          Managing accounts requires an administrator. Ask one of your admins to
          add you or change your role.
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      <div className="mx-auto max-w-5xl space-y-6">
        {/* ── the one-time password, shown once ── */}
        {issued && (
          <div className="lf-card border-emerald-500/30 bg-emerald-500/10 p-5">
            <div className="mb-2 flex items-center gap-2">
              <span className="material-symbols-outlined text-emerald-300">key</span>
              <h3 className="font-bold text-emerald-100">
                Temporary password for {issued.email}
              </h3>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <code className="select-all rounded-lg border border-emerald-500/30 bg-slate-950/70 px-3 py-2 font-mono text-lg tracking-wide text-emerald-200">
                {issued.password}
              </code>
              <button
                type="button"
                onClick={() => {
                  void navigator.clipboard?.writeText(issued.password);
                  pushToast("Copied.", "success");
                }}
                className="lf-btn-secondary inline-flex items-center gap-1.5 px-3 py-2 text-xs"
              >
                <span className="material-symbols-outlined text-base">content_copy</span>
                Copy
              </button>
              <button
                type="button"
                onClick={() => setIssued(null)}
                className="text-xs font-semibold text-emerald-300/70 hover:text-emerald-200"
              >
                Dismiss
              </button>
            </div>
            <p className="mt-3 text-xs leading-relaxed text-emerald-200/80">
              {issued.emailed
                ? "Also emailed to them. "
                : "Email is not configured, so pass this on yourself. "}
              It is shown <strong>once</strong> — the server keeps only a hash. They
              will be asked to choose their own password on first sign-in.
            </p>
          </div>
        )}

        {/* ── create ── */}
        <form onSubmit={onCreate} className="lf-card space-y-4 p-6">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary">person_add</span>
            <h3 className="text-lg font-bold text-slate-100">Create an account</h3>
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <label className="block sm:col-span-2">
              <span className="mb-1.5 block text-xs font-bold uppercase tracking-wider text-slate-400">
                Email
              </span>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="analyst@listenfirstmedia.com"
                className="w-full rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-2.5 text-sm text-slate-100 outline-none transition focus:border-primary/50"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-bold uppercase tracking-wider text-slate-400">
                Role
              </span>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="w-full rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-2.5 text-sm text-slate-100 outline-none transition focus:border-primary/50"
              >
                <option value="analyst">Analyst</option>
                <option value="admin">Admin</option>
              </select>
            </label>
          </div>

          <label className="block">
            <span className="mb-1.5 block text-xs font-bold uppercase tracking-wider text-slate-400">
              Name <span className="font-normal normal-case text-slate-600">(optional)</span>
            </span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-2.5 text-sm text-slate-100 outline-none transition focus:border-primary/50"
            />
          </label>

          {error && (
            <p role="alert" className="text-sm text-rose-300">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={creating}
            className="lf-login-btn inline-flex cursor-pointer items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-bold text-slate-950 transition disabled:cursor-wait disabled:opacity-60"
          >
            <span className="material-symbols-outlined text-lg">
              {creating ? "progress_activity" : "add"}
            </span>
            {creating ? "Creating…" : "Create account"}
          </button>
          <p className="text-xs text-slate-500">
            A temporary password is generated automatically and must be replaced
            by the account holder on first sign-in.
          </p>
        </form>

        {/* ── list ── */}
        <div className="lf-card overflow-hidden">
          <div className="flex items-center gap-2 border-b border-slate-800 px-6 py-4">
            <span className="material-symbols-outlined text-primary">group</span>
            <h3 className="text-lg font-bold text-slate-100">
              Accounts {users.length > 0 && <span className="text-slate-500">({users.length})</span>}
            </h3>
          </div>

          {loading ? (
            <p className="p-6 text-sm text-slate-400">Loading…</p>
          ) : users.length === 0 ? (
            <p className="p-6 text-sm text-slate-400">No accounts yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wider text-slate-500">
                  <tr className="border-b border-slate-800">
                    <th className="px-6 py-3 font-bold">Email</th>
                    <th className="px-6 py-3 font-bold">Role</th>
                    <th className="px-6 py-3 font-bold">Status</th>
                    <th className="px-6 py-3 font-bold">Last sign-in</th>
                    <th className="px-6 py-3" />
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id} className="border-b border-slate-800/60 last:border-0">
                      <td className="px-6 py-3">
                        <span className="font-medium text-slate-200">{u.email}</span>
                        {u.name && <span className="block text-xs text-slate-500">{u.name}</span>}
                      </td>
                      <td className="px-6 py-3">
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-bold ${
                            u.role === "admin"
                              ? "bg-primary/15 text-primary"
                              : "bg-slate-800 text-slate-400"
                          }`}
                        >
                          {u.role}
                        </span>
                      </td>
                      <td className="px-6 py-3">
                        {!u.is_active ? (
                          <span className="text-xs font-semibold text-rose-300">Disabled</span>
                        ) : u.must_change_password ? (
                          <span className="text-xs font-semibold text-amber-300">
                            Temp password
                          </span>
                        ) : (
                          <span className="text-xs font-semibold text-emerald-300">Active</span>
                        )}
                      </td>
                      <td className="px-6 py-3 text-xs text-slate-500">
                        {u.last_login_at
                          ? new Date(u.last_login_at).toLocaleString()
                          : "never"}
                      </td>
                      <td className="px-6 py-3 text-right">
                        <div className="inline-flex gap-2">
                          <button
                            type="button"
                            onClick={() => void onResetPassword(u)}
                            className="lf-btn-secondary px-2.5 py-1.5 text-xs"
                            title="Issue a new temporary password"
                          >
                            Reset password
                          </button>
                          <button
                            type="button"
                            onClick={() => void onToggleActive(u)}
                            className="lf-btn-secondary px-2.5 py-1.5 text-xs"
                          >
                            {u.is_active ? "Disable" : "Enable"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative flex min-h-screen flex-col md:flex-row">
      <AppSidebar />
      <AppMain className="relative z-10 flex-1 p-4 pb-32 md:p-8">
        <AppPageHeader title="Users" subtitle="Accounts and access" icon="manage_accounts" />
        {children}
      </AppMain>
      <AppMobileNav />
    </div>
  );
}
