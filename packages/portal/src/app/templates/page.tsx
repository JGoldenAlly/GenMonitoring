'use client';

import { useCallback, useEffect, useState } from 'react';
import { RequireAuth, useAuth } from '@/lib/auth-context';
import {
  ApiError,
  RegisterRole,
  TemplateOut,
  TemplateRegisterSpec,
  createTemplate,
  deleteTemplate,
  listTemplates,
  updateTemplate,
} from '@/lib/api';
import Badge from '@/components/Badge';
import ErrorBanner from '@/components/ErrorBanner';
import Modal from '@/components/Modal';

const REGISTER_TYPES: { value: number; label: string }[] = [
  { value: 1, label: '1 — Coil (read/write)' },
  { value: 2, label: '2 — Discrete input' },
  { value: 3, label: '3 — Holding register' },
  { value: 4, label: '4 — Input register' },
];

function emptyRegister(): TemplateRegisterSpec {
  return { address: 0, label: '', unit: '', register_type: 4, register_count: 1, read_interval_seconds: 10, role: null };
}

function TemplatesPageInner() {
  const { hasRole } = useAuth();
  const [templates, setTemplates] = useState<TemplateOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<TemplateOut | 'new' | null>(null);

  const canManage = hasRole('admin', 'operator');
  const isAdmin = hasRole('admin');

  const refresh = useCallback(async () => {
    try {
      setTemplates(await listTemplates());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load templates.');
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  function canEditTemplate(t: TemplateOut) {
    if (!canManage) return false;
    if (t.is_builtin && !isAdmin) return false;
    return true;
  }

  async function onDelete(t: TemplateOut) {
    if (!confirm(`Delete template "${t.name}"? This cannot be undone.`)) return;
    try {
      await deleteTemplate(t.id);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to delete template.');
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Templates</h1>
        {canManage && (
          <button
            onClick={() => setEditing('new')}
            className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
          >
            + New template
          </button>
        )}
      </div>

      <ErrorBanner message={error} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {templates === null && <div className="text-sm text-neutral-500">Loading templates…</div>}
        {templates?.map((t) => (
          <div key={t.id} className="rounded-lg border border-surface-border bg-surface-card p-4">
            <div className="mb-1 flex items-center justify-between">
              <h2 className="font-semibold">{t.name}</h2>
              <div className="flex gap-2">
                {t.is_builtin && <Badge tone="blue">builtin</Badge>}
                <Badge>{t.category}</Badge>
              </div>
            </div>
            {t.description && <p className="mb-2 text-sm text-neutral-400">{t.description}</p>}
            <p className="mb-3 text-xs text-neutral-600">slug: {t.slug}</p>
            <div className="mb-3 overflow-x-auto">
              <table>
                <thead>
                  <tr>
                    <th>Addr</th>
                    <th>Label</th>
                    <th>Unit</th>
                    <th>Type</th>
                    <th>Count</th>
                    <th>Interval</th>
                    <th>Role</th>
                  </tr>
                </thead>
                <tbody>
                  {t.registers.map((r, i) => (
                    <tr key={i}>
                      <td>{r.address}</td>
                      <td>{r.label}</td>
                      <td>{r.unit || '-'}</td>
                      <td>{r.register_type}</td>
                      <td>{r.register_count}</td>
                      <td>{r.read_interval_seconds}s</td>
                      <td>{r.role || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setEditing(t)}
                disabled={!canEditTemplate(t)}
                title={t.is_builtin && !isAdmin ? 'Only an admin can edit builtin templates' : undefined}
                className="rounded border border-surface-border px-3 py-1.5 text-sm text-neutral-300 hover:bg-surface-raised disabled:opacity-40"
              >
                Edit
              </button>
              <button
                onClick={() => onDelete(t)}
                disabled={!canEditTemplate(t)}
                title={t.is_builtin && !isAdmin ? 'Only an admin can delete builtin templates' : undefined}
                className="rounded border border-red-900 px-3 py-1.5 text-sm text-red-300 hover:bg-red-950/40 disabled:opacity-40"
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>

      {editing && (
        <TemplateEditor
          template={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await refresh();
          }}
        />
      )}
    </div>
  );
}

function TemplateEditor({
  template,
  onClose,
  onSaved,
}: {
  template: TemplateOut | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(template?.name || '');
  const [description, setDescription] = useState(template?.description || '');
  const [category, setCategory] = useState(template?.category || 'generator');
  const [registers, setRegisters] = useState<TemplateRegisterSpec[]>(
    template?.registers?.length ? template.registers.map((r) => ({ ...r })) : [emptyRegister()]
  );
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function updateRegister(i: number, patch: Partial<TemplateRegisterSpec>) {
    setRegisters((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }

  function removeRegister(i: number) {
    setRegisters((prev) => prev.filter((_, idx) => idx !== i));
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError('Name is required.');
      return;
    }
    if (registers.length === 0) {
      setError('Add at least one register.');
      return;
    }
    setSubmitting(true);
    try {
      const payload = {
        name: name.trim(),
        description: description.trim() || null,
        category: category.trim() || 'general',
        registers,
      };
      if (template) {
        await updateTemplate(template.id, payload);
      } else {
        await createTemplate(payload);
      }
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save template.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={template ? `Edit ${template.name}` : 'New template'} wide>
      <ErrorBanner message={error} />
      <form onSubmit={onSubmit} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} required className="w-full" />
          </div>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Category</label>
            <input value={category} onChange={(e) => setCategory(e.target.value)} className="w-full" />
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Description</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="w-full" />
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between">
            <label className="text-xs uppercase tracking-wide text-neutral-500">Registers</label>
            <button
              type="button"
              onClick={() => setRegisters((prev) => [...prev, emptyRegister()])}
              className="rounded border border-surface-border px-2 py-1 text-xs text-neutral-300 hover:bg-surface-raised"
            >
              + Add register
            </button>
          </div>
          <div className="max-h-80 space-y-2 overflow-y-auto">
            {registers.map((r, i) => (
              <div key={i} className="grid grid-cols-12 items-end gap-2 rounded border border-surface-border p-2">
                <div className="col-span-1">
                  <label className="mb-1 block text-[10px] uppercase text-neutral-500">Addr</label>
                  <input
                    type="number"
                    value={r.address}
                    onChange={(e) => updateRegister(i, { address: Number(e.target.value) })}
                    className="w-full"
                  />
                </div>
                <div className="col-span-3">
                  <label className="mb-1 block text-[10px] uppercase text-neutral-500">Label</label>
                  <input value={r.label} onChange={(e) => updateRegister(i, { label: e.target.value })} className="w-full" required />
                </div>
                <div className="col-span-2">
                  <label className="mb-1 block text-[10px] uppercase text-neutral-500">Unit</label>
                  <input value={r.unit || ''} onChange={(e) => updateRegister(i, { unit: e.target.value })} className="w-full" />
                </div>
                <div className="col-span-2">
                  <label className="mb-1 block text-[10px] uppercase text-neutral-500">Type</label>
                  <select
                    value={r.register_type}
                    onChange={(e) => updateRegister(i, { register_type: Number(e.target.value) })}
                    className="w-full"
                  >
                    {REGISTER_TYPES.map((rt) => (
                      <option key={rt.value} value={rt.value}>
                        {rt.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="col-span-1">
                  <label className="mb-1 block text-[10px] uppercase text-neutral-500">Count</label>
                  <input
                    type="number"
                    min={1}
                    value={r.register_count}
                    onChange={(e) => updateRegister(i, { register_count: Number(e.target.value) })}
                    className="w-full"
                  />
                </div>
                <div className="col-span-1">
                  <label className="mb-1 block text-[10px] uppercase text-neutral-500">Interval (s)</label>
                  <input
                    type="number"
                    min={1}
                    value={r.read_interval_seconds}
                    onChange={(e) => updateRegister(i, { read_interval_seconds: Number(e.target.value) })}
                    className="w-full"
                  />
                </div>
                <div className="col-span-1">
                  <label className="mb-1 block text-[10px] uppercase text-neutral-500">Role</label>
                  <select
                    value={r.role || ''}
                    onChange={(e) => updateRegister(i, { role: (e.target.value || null) as RegisterRole | null })}
                    className="w-full"
                  >
                    <option value="">-</option>
                    <option value="running_status">running_status</option>
                    <option value="alarm">alarm</option>
                  </select>
                </div>
                <div className="col-span-1 flex justify-end">
                  <button
                    type="button"
                    onClick={() => removeRegister(i)}
                    className="rounded border border-red-900 px-2 py-1 text-xs text-red-300 hover:bg-red-950/40"
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
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
            {submitting ? 'Saving…' : 'Save template'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export default function TemplatesPage() {
  return (
    <RequireAuth>
      <TemplatesPageInner />
    </RequireAuth>
  );
}
