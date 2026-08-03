'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  AuthUser,
  getCurrentUser,
  isAuthenticated,
  login as apiLogin,
  logout as apiLogout,
  onAuthChange,
  Role,
} from './api';

interface AuthContextValue {
  user: AuthUser | null;
  authed: boolean;
  ready: boolean;
  login: (email: string, password: string) => Promise<AuthUser>;
  logout: () => void;
  hasRole: (...roles: Role[]) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setUser(getCurrentUser());
    setReady(true);
    const unsubscribe = onAuthChange(() => {
      setUser(getCurrentUser());
    });
    return unsubscribe;
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const u = await apiLogin(email, password);
    setUser(u);
    return u;
  }, []);

  const logout = useCallback(() => {
    apiLogout();
  }, []);

  const hasRole = useCallback(
    (...roles: Role[]) => {
      if (!user) return false;
      return roles.includes(user.role);
    },
    [user]
  );

  const value = useMemo<AuthContextValue>(
    () => ({ user, authed: !!user && isAuthenticated(), ready, login, logout, hasRole }),
    [user, ready, login, logout, hasRole]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

/** Wrap any page that requires an authenticated session. Redirects to
 * /login (preserving the intended destination) if the user is not signed
 * in. Optionally restrict further to a set of roles. */
export function RequireAuth({
  children,
  roles,
}: {
  children: React.ReactNode;
  roles?: Role[];
}) {
  const { user, ready } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!ready) return;
    if (!user) {
      const next = typeof window !== 'undefined' ? window.location.pathname : '/devices';
      router.replace(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [ready, user, router]);

  if (!ready || !user) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-neutral-500">
        Checking session…
      </div>
    );
  }

  if (roles && !roles.includes(user.role)) {
    return (
      <div className="rounded border border-red-900 bg-red-950/40 p-6 text-red-200">
        You do not have permission to view this page.
      </div>
    );
  }

  return <>{children}</>;
}
