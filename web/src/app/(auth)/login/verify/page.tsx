import { redirect } from "next/navigation";

/**
 * Adres z wiadomości e-mail (`/login/verify?token=...`, tak buduje go backend) prowadzi do formularza
 * logowania, który sam wymienia token na sesję. Token nie jest nigdzie zapisywany ani logowany.
 */
export default async function VerifyRedirectPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string | string[] }>;
}) {
  const { token } = await searchParams;
  const value = Array.isArray(token) ? token[0] : token;
  redirect(value ? `/login?token=${encodeURIComponent(value)}` : "/login");
}
