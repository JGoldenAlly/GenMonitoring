'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { RequireAuth } from '@/lib/auth-context';
import { ApiError, applyTemplate, ModbusTransport, TemplateOut, listTemplates } from '@/lib/api';
import ErrorBanner from '@/components/ErrorBanner';

function AddGeneratorInner() {
  const params = useParams<{ deviceKey: string }>();
  const deviceKey = decodeURIComponent(params.deviceKey);
  const router = useRouter();

  const [templates, setTemplates] = useState<TemplateOut[] | null>(null);
  const [templateId, setTemplateId] = useState('');
  const [friendlyName, setFriendlyName] = useState('');
  const [transport, setTransport] = useState<ModbusTransport>('tcp');
  const [host, setHost] = useState('');
  const [port, setPort] = useState(502);
  const [baud, setBaud] = useState(9600);
  const [parity, setParity] = useState('N');
  const [stopBits, setStopBits] = useState(1);
  const [slaveId, setSlaveId] = useState(1);
  const [gpioOut, setGpioOut] = useState('');
  const [gpioIn, setGpioIn] = useState('');
  const [startStopEnabled, setStartStopEnabled] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    listTemplates()
      .then((data) => {
        setTemplates(data);
        if (data.length > 0) setTemplateId(data[0].id);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load templates.'));
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!templateId) {
      setError('Choose a template.');
      return;
    }
    setSubmitting(true);
    try {
      const generator = await applyTemplate(deviceKey, {
        template_id: templateId,
        modbus_transport: transport,
        modbus_host: transport === 'tcp' ? host : null,
        modbus_port: transport === 'tcp' ? port : null,
        modbus_baud: transport === 'rtu' ? baud : null,
        modbus_parity: transport === 'rtu' ? parity : null,
        modbus_stop_bits: transport === 'rtu' ? stopBits : null,
        modbus_slave_id: slaveId,
        friendly_name: friendlyName || null,
        gpio_out_channel: gpioOut || null,
        gpio_in_channel: gpioIn || null,
        start_stop_enabled: startStopEnabled,
      });
      router.push(`/generators/${generator.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to apply template.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-2xl">
      <h1 className="mb-1 text-xl font-semibold">Add generator</h1>
      <p className="mb-6 text-sm text-neutral-500">
        Applying a register template to device <span className="font-mono text-neutral-300">{deviceKey}</span>.
      </p>

      <ErrorBanner message={error} />

      <form onSubmit={onSubmit} className="space-y-5 rounded-lg border border-surface-border bg-surface-card p-5">
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Template</label>
          {templates === null ? (
            <div className="text-sm text-neutral-500">Loading templates…</div>
          ) : (
            <select value={templateId} onChange={(e) => setTemplateId(e.target.value)} className="w-full">
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} {t.is_builtin ? '(builtin)' : ''} — {t.registers.length} registers
                </option>
              ))}
            </select>
          )}
        </div>

        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">
            Friendly name (optional override)
          </label>
          <input value={friendlyName} onChange={(e) => setFriendlyName(e.target.value)} className="w-full" placeholder="Main house generator" />
        </div>

        <div>
          <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">Modbus transport</div>
          <div className="flex gap-4 text-sm">
            <label className="flex items-center gap-2">
              <input type="radio" checked={transport === 'tcp'} onChange={() => setTransport('tcp')} />
              TCP
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" checked={transport === 'rtu'} onChange={() => setTransport('rtu')} />
              RTU (serial)
            </label>
          </div>
        </div>

        {transport === 'tcp' ? (
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Host / IP</label>
              <input value={host} onChange={(e) => setHost(e.target.value)} required className="w-full" placeholder="192.168.1.50" />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Port</label>
              <input type="number" value={port} onChange={(e) => setPort(Number(e.target.value))} required className="w-full" />
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Baud rate</label>
              <input type="number" value={baud} onChange={(e) => setBaud(Number(e.target.value))} required className="w-full" />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Parity</label>
              <select value={parity} onChange={(e) => setParity(e.target.value)} className="w-full">
                <option value="N">None</option>
                <option value="E">Even</option>
                <option value="O">Odd</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Stop bits</label>
              <select value={stopBits} onChange={(e) => setStopBits(Number(e.target.value))} className="w-full">
                <option value={1}>1</option>
                <option value={2}>2</option>
              </select>
            </div>
          </div>
        )}

        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Modbus slave ID</label>
          <input type="number" min={1} value={slaveId} onChange={(e) => setSlaveId(Number(e.target.value))} required className="w-32" />
        </div>

        <div className="border-t border-surface-border pt-4">
          <div className="mb-2 flex items-center gap-2">
            <input
              id="start-stop"
              type="checkbox"
              checked={startStopEnabled}
              onChange={(e) => setStartStopEnabled(e.target.checked)}
              className="h-4 w-4"
            />
            <label htmlFor="start-stop" className="text-sm text-neutral-300">
              Enable remote start/stop control for this generator (uses the device&apos;s GPIO OUT1/IN1 pair — only
              one generator per device may have this enabled)
            </label>
          </div>
          {startStopEnabled && (
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">GPIO out channel</label>
                <input value={gpioOut} onChange={(e) => setGpioOut(e.target.value)} placeholder="OUT1" className="w-full" />
              </div>
              <div>
                <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">GPIO in channel</label>
                <input value={gpioIn} onChange={(e) => setGpioIn(e.target.value)} placeholder="IN1" className="w-full" />
              </div>
            </div>
          )}
        </div>

        <button
          type="submit"
          disabled={submitting || !templateId}
          className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          {submitting ? 'Applying template…' : 'Add generator'}
        </button>
      </form>
    </div>
  );
}

export default function AddGeneratorPage() {
  return (
    <RequireAuth roles={['admin', 'operator']}>
      <AddGeneratorInner />
    </RequireAuth>
  );
}
