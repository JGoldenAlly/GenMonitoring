export default function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="mb-4 rounded border border-red-900 bg-red-950/40 px-4 py-2 text-sm text-red-200">
      {message}
    </div>
  );
}
