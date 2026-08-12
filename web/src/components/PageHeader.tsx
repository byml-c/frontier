import { Box } from "@mui/material";
import { useContext, useLayoutEffect, useRef } from "react";
import { PageHeaderContext, type PageHeaderContent } from "./PageHeaderContext";

export function PageHeader({ eyebrow, title, description, tabs, actions }: PageHeaderContent) {
  const context = useContext(PageHeaderContext);
  const register = context?.register;
  const unregister = context?.unregister;
  const registrationId = useRef(Symbol("page-header"));

  useLayoutEffect(() => {
    if (!register || !unregister) return;
    const id = registrationId.current;
    register({ registrationId: id, eyebrow, title, description, tabs, actions });
    return () => unregister(id);
  }, [actions, description, eyebrow, register, tabs, title, unregister]);

  // The marker lets Shell reduce the old page-level top spacing without coupling
  // the layout to individual routes. Outside Shell, PageHeader remains harmless.
  return <Box data-page-header-anchor aria-hidden sx={{ display: "none" }} />;
}
