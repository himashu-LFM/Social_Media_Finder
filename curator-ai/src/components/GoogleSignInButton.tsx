"use client";

import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";
import { googleSignIn } from "@/lib/auth";

/**
 * Google Identity Services button.
 *
 * Renders nothing unless the server reports a configured client ID, so an
 * install without Google set up shows a clean password-only page rather than a
 * button that fails when pressed.
 *
 * The browser receives an ID token and forwards it untouched; `/api/auth/google`
 * verifies it against Google's public keys and decides whether that person is
 * allowed in. Nothing here is trusted.
 */

type CredentialResponse = { credential?: string };

type GoogleIdApi = {
  initialize: (config: {
    client_id: string;
    callback: (response: CredentialResponse) => void;
    auto_select?: boolean;
    cancel_on_tap_outside?: boolean;
  }) => void;
  renderButton: (parent: HTMLElement, options: Record<string, unknown>) => void;
};

declare global {
  interface Window {
    google?: { accounts?: { id?: GoogleIdApi } };
  }
}

export function GoogleSignInButton({
  clientId,
  onSignedIn,
  onError,
}: {
  clientId: string;
  onSignedIn: () => void;
  onError: (message: string) => void;
}) {
  const target = useRef<HTMLDivElement>(null);
  const [scriptReady, setScriptReady] = useState(false);

  // Kept in a ref so `initialize` never captures a stale callback: Google holds
  // onto whatever function it was given for the lifetime of the page.
  const handlers = useRef({ onSignedIn, onError });
  useEffect(() => {
    handlers.current = { onSignedIn, onError };
  }, [onSignedIn, onError]);

  const render = useCallback(() => {
    const api = window.google?.accounts?.id;
    if (!api || !target.current) return;
    api.initialize({
      client_id: clientId,
      callback: (response: CredentialResponse) => {
        if (!response.credential) {
          handlers.current.onError("Google did not return a sign-in token.");
          return;
        }
        void googleSignIn(response.credential)
          .then(() => handlers.current.onSignedIn())
          .catch((err: unknown) =>
            handlers.current.onError(
              err instanceof Error ? err.message : "Google sign-in failed.",
            ),
          );
      },
      cancel_on_tap_outside: true,
    });
    api.renderButton(target.current, {
      theme: "filled_black",
      size: "large",
      shape: "pill",
      text: "signin_with",
      width: 320,
    });
  }, [clientId]);

  useEffect(() => {
    if (scriptReady) render();
  }, [scriptReady, render]);

  return (
    <>
      <Script
        src="https://accounts.google.com/gsi/client"
        strategy="afterInteractive"
        onReady={() => setScriptReady(true)}
        onError={() => onError("Could not load Google sign-in.")}
      />
      <div ref={target} className="flex justify-center" />
    </>
  );
}
