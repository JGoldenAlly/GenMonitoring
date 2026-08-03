'use client';

import { useCallback, useEffect, useState } from 'react';
import { RequireAuth, useAuth } from '@/lib/auth-context';
import {
  ApiError,
  Role,
  UserOut,
  createUser,
  deleteUser,
  listUsers,
  resetUserPassword,
  updateUser,
} from '@/lib/api';
import Badge from '@/components/Badge';
import ErrorBanner from '@/components/ErrorBanner';
import Modal from '@/components/Modal';
import { formatDateTime } from '@/lib/format';

function UsersPageInner() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<UserOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<UserOut | null>(null);
  const [resetting, setResetting] = useState<UserOut | null>(null);

  const refresh = useCallback(async () => {
    try {
      setUsers(await listUsers());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load users.');
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function onDelete(u: UserOut) {
    if (u.id === me?.id) {
      alert("You can't delete your own account.");
      return;
    }
    if (!confirm(`Delete user ${u.email}?`)) return;
    try {
      await deleteUser(u.id);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to delete user.');
    }
  }

  async function onToggleActive(u: UserOut) {
    try {
      await updateUser(u.id, { is_active: !u.is_active });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update user.');
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Users</h1>
        <button
          onClick={() => setShowCreate(true)}
          className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
        >
          + New user
        </button>
      </div>

      <ErrorBanner message={error} />

      <div className="overflow-x-auto rounded-lg border border-surface-border">
        <table>
          <thead>
            <tr>
              <th>Email</th>
              <th>Role</th>
              <th>Status</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {users === null && (
              <tr>
                <td colSpan={5} className="text-neutral-500">
                  Loading users…
                </td>
              </tr>
            )}
            {users?.map((u) => (
              <tr key={u.id}>
                <td>{u.email}</td>
                <td className="uppercase">{u.role}</td>
                <td>
                  <button onClick={() => onToggleActive(u)} title="Click to toggle">
                    {u.is_active ? <Badge tone="green">active</Badge> : <Badge tone="red">disabled</Badge>}
                  </button>
                </td>
                <td>{formatDateTime(u.created_at)}</td>
                <td className="space-x-2">
                  <button
                    onClick={() => setEditing(u)}
                    className="rounded border border-surface-border px-2 py-1 text-xs text-neutral-300 hover:bg-surface-raised"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => setResetting(u)}
                    className="rounded border border-surface-border px-2 py-1 text-xs text-neutral-300 hover:bg-surface-raised"
                  >
                    Reset password
                  </button>
                  <button
                    onClick={() => onDelete(u)}
                    className="rounded border border-red-900 px-2 py-1 text-xs text-red-300 hover:bg-red-950/40"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <CreateUserModal
          onClose={() => setShowCreate(false)}
          onSaved={async () => {
            setShowCreate(false);
            await refresh();
          }}
        />
      )}
      {editing && (
        <EditUserModal
          user={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await refresh();
          }}
        />
      )}
      {resetting && <ResetPasswordModal user={resetting} onClose={() => setResetting(null)} />}
    </div>
  );
}

function CreateUserModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<Role>('operator');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    setSubmitting(true);
    try {
      await createUser({ email, password, role, is_active: true });
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to create user.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open onClose={onClose} title="New user">
      <ErrorBanner message={error} />
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Email</label>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="w-full" />
        </div>
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Temporary password</label>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full"
            minLength={8}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Role</label>
          <select value={role} onChange={(e) => setRole(e.target.value as Role)} className="w-full">
            <option value="admin">admin</option>
            <option value="operator">operator</option>
            <option value="viewer">viewer</option>
          </select>
        </div>
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} className="rounded border border-surface-border px-4 py-2 text-sm text-neutral-300">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {submitting ? 'Creating…' : 'Create user'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function EditUserModal({
  user,
  onClose,
  onSaved,
}: {
  user: UserOut;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [email, setEmail] = useState(user.email);
  const [role, setRole] = useState<Role>(user.role);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await updateUser(user.id, { email, role });
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update user.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={`Edit ${user.email}`}>
      <ErrorBanner message={error} />
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Email</label>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="w-full" />
        </div>
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Role</label>
          <select value={role} onChange={(e) => setRole(e.target.value as Role)} className="w-full">
            <option value="admin">admin</option>
            <option value="operator">operator</option>
            <option value="viewer">viewer</option>
          </select>
        </div>
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} className="rounded border border-surface-border px-4 py-2 text-sm text-neutral-300">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {submitting ? 'Saving…' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ResetPasswordModal({ user, onClose }: { user: UserOut; onClose: () => void }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    setSubmitting(true);
    try {
      await resetUserPassword(user.id, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reset password.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={`Reset password — ${user.email}`}>
      <ErrorBanner message={error} />
      {done ? (
        <div className="space-y-4">
          <p className="text-sm text-emerald-400">Password reset. Share the new password with the user securely.</p>
          <button onClick={onClose} className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500">
            Close
          </button>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">New password</label>
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full"
            />
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" onClick={onClose} className="rounded border border-surface-border px-4 py-2 text-sm text-neutral-300">
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="rounded bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-500 disabled:opacity-50"
            >
              {submitting ? 'Resetting…' : 'Reset password'}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}

export default function UsersPage() {
  return (
    <RequireAuth roles={['admin']}>
      <UsersPageInner />
    </RequireAuth>
  );
}
