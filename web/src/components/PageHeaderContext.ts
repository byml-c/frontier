import { createContext, type ReactNode, useContext } from "react";

export interface PageHeaderContent {
  eyebrow?: string;
  title: string;
  description?: string;
  tabs?: ReactNode;
  actions?: ReactNode;
}

export interface RegisteredPageHeader extends PageHeaderContent {
  registrationId: symbol;
}

export interface PageHeaderContextValue {
  content: PageHeaderContent | null;
  register: (content: RegisteredPageHeader) => void;
  unregister: (registrationId: symbol) => void;
}

export const PageHeaderContext = createContext<PageHeaderContextValue | null>(null);

export function usePageHeaderContent() {
  return useContext(PageHeaderContext)?.content ?? null;
}
