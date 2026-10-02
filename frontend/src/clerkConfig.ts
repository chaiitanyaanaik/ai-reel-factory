/** Whether Clerk publishable key is configured for this Vite build. */
export function isClerkConfigured(): boolean {
  const pk = (import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || "").trim();
  if (!pk.startsWith("pk_")) return false;
  const bad = ["your_key", "replace_me", "replace-me"];
  return !bad.some((s) => pk.toLowerCase().includes(s));
}

export function clerkPublishableKey(): string {
  return (import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || "").trim();
}
