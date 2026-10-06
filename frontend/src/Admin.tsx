import { useCallback, useEffect, useState } from "react";
import {
  listAdminUsers,
  patchAdminUser,
  upsertAdminUserByEmail,
  type AdminUserRow,
} from "./api";

type Draft = {
  plan: "free" | "paid";
  project_limit: string; // empty = null
  notes: string;
};

function draftFromRow(row: AdminUserRow): Draft {
  return {
    plan: row.plan === "paid" ? "paid" : "free",
    project_limit: row.project_limit == null ? "" : String(row.project_limit),
    notes: row.entitlement?.notes || "",
  };
}

function parseLimit(raw: string): number | null {
  const t = raw.trim();
  if (!t) return null;
  const n = Number(t);
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.floor(n);
}

export default function Admin({
  onError,
  onStatus,
}: {
  onError: (msg: string) => void;
  onStatus: (msg: string) => void;
}) {
  const [rows, setRows] = useState<AdminUserRow[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [loading, setLoading] = useState(true);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [newEmail, setNewEmail] = useState("");
  const [newPlan, setNewPlan] = useState<"free" | "paid">("paid");
  const [newLimit, setNewLimit] = useState("");

  const rowKey = (r: AdminUserRow) => r.user_id || `email:${r.email || ""}`;

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const users = await listAdminUsers();
      setRows(users);
      const next: Record<string, Draft> = {};
      for (const u of users) {
        next[rowKey(u)] = draftFromRow(u);
      }
      setDrafts(next);
    } catch (e) {
      onError(String((e as Error).message || e));
    } finally {
      setLoading(false);
    }
  }, [onError]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function saveRow(row: AdminUserRow) {
    const key = rowKey(row);
    const draft = drafts[key] || draftFromRow(row);
    setSavingKey(key);
    onError("");
    onStatus("");
    try {
      const body = {
        plan: draft.plan,
        project_limit: parseLimit(draft.project_limit),
        notes: draft.notes,
      };
      if (row.user_id) {
        await patchAdminUser(row.user_id, body);
      } else if (row.email) {
        await upsertAdminUserByEmail({ email: row.email, ...body });
      } else {
        throw new Error("User has no id or email");
      }
      onStatus(`Saved plan for ${row.email || row.user_id}`);
      await refresh();
    } catch (e) {
      onError(String((e as Error).message || e));
    } finally {
      setSavingKey(null);
    }
  }

  async function addByEmail(e: React.FormEvent) {
    e.preventDefault();
    onError("");
    onStatus("");
    setSavingKey("new");
    try {
      await upsertAdminUserByEmail({
        email: newEmail.trim(),
        plan: newPlan,
        project_limit: parseLimit(newLimit),
        notes: "",
      });
      setNewEmail("");
      setNewLimit("");
      setNewPlan("paid");
      onStatus(`Updated plan for ${newEmail.trim()}`);
      await refresh();
    } catch (err) {
      onError(String((err as Error).message || err));
    } finally {
      setSavingKey(null);
    }
  }

  return (
    <div className="card admin-card">
      <div className="library-hero">
        <h1 className="page-title">Admin</h1>
        <p className="page-sub">
          Mark users paid (unlimited) or free, or set a custom lifetime project limit. Empty limit =
          unlimited for paid, or the global free default for free users.
        </p>
      </div>

      <form className="admin-add" onSubmit={(ev) => void addByEmail(ev)}>
        <input
          type="email"
          required
          placeholder="user@email.com"
          value={newEmail}
          onChange={(e) => setNewEmail(e.target.value)}
          aria-label="Email"
        />
        <select
          value={newPlan}
          onChange={(e) => setNewPlan(e.target.value as "free" | "paid")}
          aria-label="Plan"
        >
          <option value="paid">Paid</option>
          <option value="free">Free</option>
        </select>
        <input
          type="number"
          min={0}
          placeholder="Limit (blank = ∞ / default)"
          value={newLimit}
          onChange={(e) => setNewLimit(e.target.value)}
          aria-label="Project limit"
        />
        <button type="submit" className="btn btn-primary" disabled={savingKey === "new"}>
          Set plan
        </button>
      </form>

      {loading ? (
        <p className="muted">Loading users…</p>
      ) : rows.length === 0 ? (
        <p className="muted">No users yet. They appear after sign-in, or add by email above.</p>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Used</th>
                <th>Plan</th>
                <th>Limit</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const key = rowKey(row);
                const draft = drafts[key] || draftFromRow(row);
                return (
                  <tr key={key}>
                    <td>
                      <div className="admin-user">
                        <strong>{row.email || "—"}</strong>
                        <span className="muted">
                          {row.name ? `${row.name} · ` : ""}
                          {row.user_id || "pending invite"}
                          {row.pending ? " · pending" : ""}
                        </span>
                      </div>
                    </td>
                    <td>{row.projects_used}</td>
                    <td>
                      <select
                        value={draft.plan}
                        onChange={(e) =>
                          setDrafts((d) => ({
                            ...d,
                            [key]: { ...draft, plan: e.target.value as "free" | "paid" },
                          }))
                        }
                      >
                        <option value="free">Free</option>
                        <option value="paid">Paid</option>
                      </select>
                    </td>
                    <td>
                      <input
                        type="number"
                        min={0}
                        className="admin-limit"
                        placeholder={draft.plan === "paid" ? "∞" : "default"}
                        value={draft.project_limit}
                        onChange={(e) =>
                          setDrafts((d) => ({
                            ...d,
                            [key]: { ...draft, project_limit: e.target.value },
                          }))
                        }
                      />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        disabled={savingKey === key}
                        onClick={() => void saveRow(row)}
                      >
                        Save
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
