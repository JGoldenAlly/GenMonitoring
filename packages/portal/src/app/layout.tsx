import type { Metadata } from 'next';
import './globals.css';
import { AuthProvider } from '@/lib/auth-context';
import NavHeader from '@/components/NavHeader';

export const metadata: Metadata = {
  title: 'GenMonitoring',
  description: "Ally Energy's generator monitoring platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-surface text-neutral-100 antialiased">
        <AuthProvider>
          <NavHeader />
          <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
        </AuthProvider>
      </body>
    </html>
  );
}
