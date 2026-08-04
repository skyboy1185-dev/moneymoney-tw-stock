"use client";

import { LogOut } from "lucide-react";

export function PrivateSiteLogoutButton() {
  const logout = async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.replace("/login");
  };
  return <button className="private-site-logout" type="button" onClick={() => void logout()}><LogOut size={13} />登出</button>;
}
