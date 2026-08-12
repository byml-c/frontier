import { type ReactNode, useCallback, useMemo, useState } from "react";
import {
  PageHeaderContext,
  type PageHeaderContextValue,
  type RegisteredPageHeader,
} from "./PageHeaderContext";

export function PageHeaderProvider({ children }: { children: ReactNode }) {
  const [registered, setRegistered] = useState<RegisteredPageHeader | null>(null);
  const register = useCallback((content: RegisteredPageHeader) => setRegistered(content), []);
  const unregister = useCallback((registrationId: symbol) => {
    setRegistered((current) => current?.registrationId === registrationId ? null : current);
  }, []);
  const value = useMemo<PageHeaderContextValue>(() => ({
    content: registered,
    register,
    unregister,
  }), [register, registered, unregister]);

  return <PageHeaderContext.Provider value={value}>{children}</PageHeaderContext.Provider>;
}
