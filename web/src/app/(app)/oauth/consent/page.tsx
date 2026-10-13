import { ConsentPage } from "@/features/oauth/ConsentView";

/**
 * Ekran zgody OAuth (`/oauth/consent?request=<id>`): tu przekierowuje `/authorize` serwera autoryzacji, gdy
 * aplikacja (ChatGPT, Claude, ...) prosi o dostęp. Bez sesji proxy odsyła na `/login?next=...` i wraca tutaj.
 */
export default async function OAuthConsentPage({
  searchParams,
}: {
  searchParams: Promise<{ request?: string | string[] }>;
}) {
  const { request } = await searchParams;
  const value = Array.isArray(request) ? request[0] : request;
  return <ConsentPage requestId={value?.trim() ? value.trim() : null} />;
}
