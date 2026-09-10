"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
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
 * Create and manage accounts — the Users design from the handoff.
 *
 * This page is the *only* way an account comes into existence; there is no
 * public sign-up anywhere in the product. Hiding it from non-admins is a
 * courtesy — the API enforces the role, so a curl with an analyst's token gets
 * 403 whatever the interface shows.
 *
 * A newly issued temporary password is shown exactly once, because that is all
 * the server will ever hand back: it is stored only as an Argon2 hash.
 */
export default function AdminUsersPage() {
  const { pushToast } = useToast();

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const [query, setQuery] = useState("");

  const [creating, setCreating] = useState(false);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("analyst");
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState<{
    email: string;
    password: string;
    emailed: boolean;
  } | null>(null);

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
      // null means auth is switched off entirely (local dev) — permitted, as
      // the server does.
      setIsAdmin(me === null || me.role?.toLowerCase() === "admin");
      await reload();
    })();
    return () => {
      alive = false;
    };
  }, [reload]);

  const counts = useMemo(
    () => ({
      active: users.filter((u) => u.is_active && !u.must_change_password).length,
      temp: users.filter((u) => u.is_active && u.must_change_password).length,
      disabled: users.filter((u) => !u.is_active).length,
      admins: users.filter((u) => u.is_active && u.role?.toLowerCase() === "admin").length,
    }),
    [users],
  );

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) =>
        u.email.toLowerCase().includes(q) || (u.name || "").toLowerCase().includes(q),
    );
  }, [users, query]);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
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
      setCreating(false);
      pushToast("Account created.", "success");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the account.");
      pushToast("Could not create the account.", "error");
    } finally {
      setBusy(false);
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

  return (
    <AppShell
      icon="manage_accounts"
      eyebrow="Accounts and access"
      title="Users"
      actions={
        isAdmin === false ? undefined : (
          <>
            <span className="sc-pill">
              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
                group
              </span>
              {users.length} account{users.length === 1 ? "" : "s"} · {counts.admins} admin
              {counts.admins === 1 ? "" : "s"}
            </span>
            <button
              type="button"
              className="sc-btn"
              onClick={() => setCreating((v) => !v)}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
                person_add
              </span>
              Create an account
            </button>
          </>
        )
      }
    >
      <div className="sc-page">
        {isAdmin === false ? (
          <div className="sc-col">
            <section className="sc-note">
              <span className="sc-note-label">
                <span className="material-symbols-outlined" style={{ fontSize: 15 }}>
                  lock
                </span>{" "}
                Admin only
              </span>
              <p>
                Managing accounts requires an administrator. Ask one of your admins to
                add you or change your role.
              </p>
            </section>
          </div>
        ) : (
          <>
            <div className="sc-col">
              {issued && (
                <section
                  className="sc-card"
                  style={{
                    borderColor: "rgba(242,209,0,0.28)",
                    background: "rgba(242,209,0,0.06)",
                  }}
                >
                  <div className="sc-card-body">
                    <span
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        marginBottom: 10,
                        fontSize: 12,
                        fontWeight: 800,
                        color: "#f2d100",
                      }}
                    >
                      <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
                        key
                      </span>
                      Temporary password for {issued.email}
                    </span>
                    <span
                      style={{
                        display: "flex",
                        flexWrap: "wrap",
                        alignItems: "center",
                        gap: 10,
                      }}
                    >
                      <code
                        className="select-all"
                        style={{
                          padding: "7px 12px",
                          borderRadius: 8,
                          border: "1px solid rgba(242,209,0,0.3)",
                          background: "rgba(2,6,23,0.7)",
                          fontFamily: "ui-monospace, Menlo, monospace",
                          fontSize: 15,
                          letterSpacing: "0.04em",
                          color: "#f2d100",
                        }}
                      >
                        {issued.password}
                      </code>
                      <button
                        type="button"
                        className="sc-btn"
                        onClick={() => {
                          void navigator.clipboard?.writeText(issued.password);
                          pushToast("Copied.", "success");
                        }}
                      >
                        <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
                          content_copy
                        </span>
                        Copy
                      </button>
                      <button
                        type="button"
                        className="sc-btn"
                        style={{ border: 0, background: "none" }}
                        onClick={() => setIssued(null)}
                      >
                        Dismiss
                      </button>
                    </span>
                    <p className="sc-muted" style={{ margin: "12px 0 0" }}>
                      {issued.emailed
                        ? "Also emailed to them. "
                        : "Email is not configured, so pass this on yourself. "}
                      It is shown <strong style={{ color: "#e2e8f0" }}>once</strong> — the
                      server keeps only a hash. They will be asked to choose their own
                      password on first sign-in.
                    </p>
                  </div>
                </section>
              )}

              {creating && (
                <section className="sc-card">
                  <div className="sc-card-head">
                    <span className="material-symbols-outlined">person_add</span>
                    <h3 className="sc-card-title">Create an account</h3>
                    <button
                      type="button"
                      aria-label="Close"
                      className="sc-icon-btn"
                      style={{ marginLeft: "auto" }}
                      onClick={() => setCreating(false)}
                    >
                      <span className="material-symbols-outlined" style={{ fontSize: 17 }}>
                        close
                      </span>
                    </button>
                  </div>
                  <form
                    onSubmit={onCreate}
                    className="sc-card-body"
                    style={{ display: "grid", gap: 12 }}
                  >
                    <div
                      style={{
                        display: "grid",
                        gap: 12,
                        gridTemplateColumns: "minmax(0,2fr) minmax(0,1fr)",
                      }}
                    >
                      <label>
                        <span className="sc-field-label">Email</span>
                        <input
                          type="email"
                          required
                          className="sc-input"
                          value={email}
                          onChange={(e) => setEmail(e.target.value)}
                          placeholder="analyst@listenfirstmedia.com"
                        />
                      </label>
                      <label>
                        <span className="sc-field-label">Role</span>
                        <select
                          className="sc-input"
                          value={role}
                          onChange={(e) => setRole(e.target.value)}
                        >
                          <option value="analyst">Analyst</option>
                          <option value="admin">Admin</option>
                        </select>
                      </label>
                    </div>
                    <label>
                      <span className="sc-field-label">
                        Name <span style={{ textTransform: "none" }}>(optional)</span>
                      </span>
                      <input
                        type="text"
                        className="sc-input"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                      />
                    </label>
                    {error && (
                      <p role="alert" className="sc-note warn" style={{ margin: 0 }}>
                        <span className="material-symbols-outlined">error</span>
                        <span>{error}</span>
                      </p>
                    )}
                    <span
                      style={{
                        display: "flex",
                        flexWrap: "wrap",
                        alignItems: "center",
                        gap: 12,
                      }}
                    >
                      <button type="submit" disabled={busy} className="sc-btn-primary">
                        <span
                          className={`material-symbols-outlined${busy ? " animate-spin" : ""}`}
                          style={{ fontSize: 18 }}
                        >
                          {busy ? "progress_activity" : "add"}
                        </span>
                        {busy ? "Creating…" : "Create account"}
                      </button>
                      <p className="sc-muted" style={{ margin: 0, flex: "1 1 220px" }}>
                        A temporary password is generated automatically and must be
                        replaced by the account holder on first sign-in.
                      </p>
                    </span>
                  </form>
                </section>
              )}

              <section className="sc-card" style={{ overflow: "hidden" }}>
                <div className="sc-card-head">
                  <span className="material-symbols-outlined">group</span>
                  <h3 className="sc-card-title">
                    Accounts{" "}
                    <span style={{ color: "#64748b", fontWeight: 700 }}>
                      ({users.length})
                    </span>
                  </h3>
                  <span
                    style={{
                      marginLeft: "auto",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 7,
                      height: 30,
                      padding: "0 10px",
                      borderRadius: 9,
                      border: "1px solid rgba(255,255,255,0.1)",
                      background: "rgba(2,6,23,0.5)",
                    }}
                  >
                    <span
                      className="material-symbols-outlined"
                      style={{ fontSize: 15, color: "#64748b" }}
                    >
                      search
                    </span>
                    <input
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="Find an account…"
                      aria-label="Find an account"
                      style={{
                        width: 150,
                        border: 0,
                        background: "transparent",
                        fontSize: 12,
                        color: "#f1f5f9",
                        outline: "none",
                      }}
                    />
                  </span>
                </div>

                {loading ? (
                  <p className="sc-card-body sc-muted" style={{ margin: 0 }}>
                    Loading…
                  </p>
                ) : shown.length === 0 ? (
                  <p className="sc-card-body sc-muted" style={{ margin: 0 }}>
                    {users.length === 0 ? "No accounts yet." : "No account matches that."}
                  </p>
                ) : (
                  <div className="sc-table-scroll">
                    <table className="sc-table">
                      <thead>
                        <tr>
                          <th>Email</th>
                          <th>Role</th>
                          <th>Status</th>
                          <th>Last sign-in</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {shown.map((u) => (
                          <tr key={u.id}>
                            <td>
                              <span
                                style={{
                                  display: "block",
                                  fontWeight: 600,
                                  color: "#e2e8f0",
                                }}
                              >
                                {u.email}
                              </span>
                              {u.name && (
                                <span
                                  style={{
                                    display: "block",
                                    marginTop: 2,
                                    fontSize: 11,
                                    color: "#64748b",
                                  }}
                                >
                                  {u.name}
                                </span>
                              )}
                            </td>
                            <td>
                              <span
                                className="sc-chip"
                                style={
                                  u.role?.toLowerCase() === "admin"
                                    ? {
                                        borderColor: "rgba(242,209,0,0.3)",
                                        background: "rgba(242,209,0,0.12)",
                                        color: "#f2d100",
                                      }
                                    : undefined
                                }
                              >
                                {u.role}
                              </span>
                            </td>
                            <td>
                              <StatusPill user={u} />
                            </td>
                            <td style={{ fontSize: 11, color: "#64748b" }}>
                              {u.last_login_at
                                ? new Date(u.last_login_at).toLocaleString()
                                : "never"}
                            </td>
                            <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                              <span style={{ display: "inline-flex", gap: 6 }}>
                                <button
                                  type="button"
                                  className="sc-btn"
                                  title="Issue a new temporary password"
                                  onClick={() => void onResetPassword(u)}
                                >
                                  <span
                                    className="material-symbols-outlined"
                                    style={{ fontSize: 15 }}
                                  >
                                    lock_reset
                                  </span>
                                  Reset password
                                </button>
                                <button
                                  type="button"
                                  className="sc-btn"
                                  onClick={() => void onToggleActive(u)}
                                >
                                  <span
                                    className="material-symbols-outlined"
                                    style={{ fontSize: 15 }}
                                  >
                                    {u.is_active ? "block" : "check_circle"}
                                  </span>
                                  {u.is_active ? "Disable" : "Enable"}
                                </button>
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <p
                  className="sc-muted"
                  style={{
                    margin: 0,
                    padding: "12px 16px",
                    borderTop: "1px solid rgba(255,255,255,0.06)",
                    fontSize: 11.5,
                  }}
                >
                  This page is the only way an account comes into existence — there is no
                  public sign-up. The API enforces the role regardless of what the
                  interface shows.
                </p>
              </section>
            </div>

            <aside className="sc-aside">
              <section className="sc-card">
                <div className="sc-card-head" style={{ padding: "12px 14px" }}>
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 800,
                      letterSpacing: "0.14em",
                      textTransform: "uppercase",
                      color: "#f2d100",
                    }}
                  >
                    Access at a glance
                  </span>
                </div>
                <div style={{ padding: "6px 0" }}>
                  <Glance label="Active" value={counts.active} />
                  <Glance label="Temp password" value={counts.temp} tone="#fcd34d" />
                  <Glance label="Disabled" value={counts.disabled} tone="#fda4af" />
                </div>
              </section>

              <section className="sc-note">
                <span className="sc-note-label">Admin only</span>
                <p>
                  Only an administrator can reach this page. Accounts start with a
                  temporary password that the holder must replace before the app becomes
                  usable to them.
                </p>
              </section>
            </aside>
          </>
        )}
      </div>
    </AppShell>
  );
}

function StatusPill({ user }: { user: AdminUser }) {
  const [text, colour, bg] = !user.is_active
    ? ["Disabled", "#fda4af", "rgba(244,63,94,0.12)"]
    : user.must_change_password
      ? ["Temp password", "#fcd34d", "rgba(245,158,11,0.12)"]
      : ["Active", "#6ee7b7", "rgba(52,211,153,0.12)"];
  return (
    <span
      className="sc-chip"
      style={{ color: colour, background: bg, borderColor: `${colour}33` }}
    >
      {text}
    </span>
  );
}

function Glance({
  label,
  value,
  tone = "#6ee7b7",
}: {
  label: string;
  value: number;
  tone?: string;
}) {
  return (
    <span
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "9px 14px",
        borderBottom: "1px solid rgba(255,255,255,0.04)",
      }}
    >
      <span style={{ fontSize: 12, color: "#94a3b8" }}>{label}</span>
      <span style={{ fontSize: 15, fontWeight: 800, color: tone }}>{value}</span>
    </span>
  );
}
