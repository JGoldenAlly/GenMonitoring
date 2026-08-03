'use client';

import { useCallback, useEffect, useState } from 'react';
import { RequireAuth } from '@/lib/auth-context';
import { ApiError, ApiKeyOut, createApiKey, listApiKeys, revokeApiKey } from '@/lib/api';
import { formatDateTime, relativeTime } from '@/lib/format';
import ErrorBanner from '@/components/ErrorBanner';
import Modal from '@/components/Modal';

function ApiKeysPageInner() {
  const [keys, setKeys] = useState<ApiKeyOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [label, setLabel] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [rawKey, setRawKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setKeys(await listApiKeys());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load API keys.');
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const created = await createApiKey(label.trim() || undefined);
      setRawKey(created.api_key);
      setLabel('');
      setShowCreate(false);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to create API key.');
    } finally {
      setSubmitting(false);
    }
  }

  async function onRevoke(id: string) {
    if (!confirm('Revoke this API key? Any integration using it will stop working immediately.')) return;
    try {
      await revokeApiKey(id);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to revoke API key.');
    }
  }

  async function copyKey() {
    if (!rawKey) return;
    try {
      await navigator.clipboard.writeText(rawKey);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard API unavailable; user can still select-all manually
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">API keys</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Self-service read-only telemetry keys for your own account. Use these for scripts or dashboards that
            only need to read data — they cannot issue start/stop commands.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
        >
          + New key
        </button>
      </div>

      <ErrorBanner message={error} />

      <div className="overflow-x-auto rounded-lg border border-surface-border">
        <table>
          <thead>
            <tr>
              <th>Label</th>
              <th>Created</th>
              <th>Last used</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {keys === null && (
              <tr>
                <td colSpan={4} className="text-neutral-500">
                  Loading API keys…
                </td>
              </tr>
            )}
            {keys !== null && keys.length === 0 && (
              <tr>
                <td colSpan={4} className="text-neutral-500">
                  You have no API keys yet.
                </td>
              </tr>
            )}
            {keys?.map((k) => (
              <tr key={k.id}>
                <td>{k.label || <span className="text-neutral-600">(unlabeled)</span>}</td>
                <td>{formatDateTime(k.created_at)}</td>
                <td>{k.last_used_at ? relativeTime(k.last_used_at) : 'never'}</td>
                <td>
                  <button
                    onClick={() => onRevoke(k.id)}
                    className="rounded border border-red-900 px-2 py-1 text-xs text-red-300 hover:bg-red-950/40"
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <Modal open onClose={() => setShowCreate(false)} title="New API key">
          <form onSubmit={onCreate} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Label (optional)</label>
              <input value={label} onChange={(e) => setLabel(e.target.value)} className="w-full" placeholder="Grafana dashboard" />
            </div>
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setShowCreate(false)}
                className="rounded border border-surface-border px-4 py-2 text-sm text-neutral-300"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {submitting ? 'Creating…' : 'Create key'}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {rawKey && (
        <Modal open onClose={() => setRawKey(null)} title="API key created">
          <div className="space-y-4">
            <p className="rounded border border-amber-800 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
              Copy this key now — you will not be able to see it again.
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 overflow-x-auto rounded border border-surface-border bg-black/40 px-3 py-2 text-sm text-emerald-300">
                {rawKey}
              </code>
              <button
                onClick={copyKey}
                className="rounded border border-surface-border px-3 py-2 text-sm text-neutral-300 hover:bg-surface-raised"
              >
                {copied ? 'Copied!' : 'Copy'}
              </button>
            </div>
            <button
              onClick={() => setRawKey(null)}
              className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
            >
              I&apos;ve saved it
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

export default function ApiKeysPage() {
  return (
    <RequireAuth>
      <ApiKeysPageInner />
    </RequireAuth>
  );
}
