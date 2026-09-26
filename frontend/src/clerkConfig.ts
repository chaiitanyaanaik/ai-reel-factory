/** Whether Clerk publishable key is configured for this Vite build. */
export function isClerkConfigured(): boolean {
  const pk = (import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || "").trim();
  return pk.startsWith("pk_") && !pk.includes("your_key");
}

export function clerkPublishableKey(): string {
  return (import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || "").trim();
}
