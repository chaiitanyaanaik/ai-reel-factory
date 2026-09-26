import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ClerkProvider } from "@clerk/react";
import App from "./App";
import { clerkPublishableKey, isClerkConfigured } from "./clerkConfig";
import "./styles.css";

const root = createRoot(document.getElementById("root")!);

root.render(
  <StrictMode>
    {isClerkConfigured() ? (
      <ClerkProvider publishableKey={clerkPublishableKey()}>
        <App />
      </ClerkProvider>
    ) : (
      <App />
    )}
  </StrictMode>
);
