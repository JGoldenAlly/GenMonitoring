import { formatDateTime } from '@/lib/format';

export default function StatTile({
  label,
  value,
  unit,
  timestamp,
}: {
  label: string;
  value: number | null;
  unit?: string | null;
  timestamp?: string;
}) {
  return (
    <div className="rounded-lg border border-surface-border bg-surface-card p-4">
      <div className="text-xs uppercase tracking-wide text-neutral-500">{label}</div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="text-2xl font-semibold text-neutral-100">
          {value === null || value === undefined ? '-' : value}
        </span>
        {unit && <span className="text-sm text-neutral-500">{unit}</span>}
      </div>
      {timestamp && <div className="mt-1 text-xs text-neutral-600">{formatDateTime(timestamp)}</div>}
    </div>
  );
}
