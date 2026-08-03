'use client';

interface Point {
  time: string;
  value: number | null;
}

export default function Sparkline({
  points,
  width = 640,
  height = 180,
  unit,
}: {
  points: Point[];
  width?: number;
  height?: number;
  unit?: string | null;
}) {
  const valid = points.filter((p) => p.value !== null && !Number.isNaN(p.value)) as {
    time: string;
    value: number;
  }[];

  if (valid.length < 2) {
    return (
      <div
        className="flex items-center justify-center rounded border border-surface-border text-sm text-neutral-500"
        style={{ width: '100%', height }}
      >
        Not enough data points yet
      </div>
    );
  }

  const values = valid.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const padding = 8;

  const xAt = (i: number) => padding + (i / (valid.length - 1)) * (width - padding * 2);
  const yAt = (v: number) => height - padding - ((v - min) / range) * (height - padding * 2);

  const path = valid.map((p, i) => `${i === 0 ? 'M' : 'L'}${xAt(i).toFixed(1)},${yAt(p.value).toFixed(1)}`).join(' ');
  const areaPath = `${path} L${xAt(valid.length - 1).toFixed(1)},${height - padding} L${xAt(0).toFixed(1)},${height - padding} Z`;

  const last = valid[valid.length - 1];

  return (
    <div className="w-full">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ height }} preserveAspectRatio="none">
        <path d={areaPath} fill="url(#sparkline-fill)" stroke="none" />
        <path d={path} fill="none" stroke="#34d399" strokeWidth={2} />
        <defs>
          <linearGradient id="sparkline-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#34d399" stopOpacity="0.25" />
            <stop offset="100%" stopColor="#34d399" stopOpacity="0" />
          </linearGradient>
        </defs>
      </svg>
      <div className="mt-1 flex justify-between text-xs text-neutral-500">
        <span>
          min {min.toFixed(2)} / max {max.toFixed(2)} {unit || ''}
        </span>
        <span>
          latest {last.value.toFixed(2)} {unit || ''} @ {new Date(last.time).toLocaleTimeString()}
        </span>
      </div>
    </div>
  );
}
