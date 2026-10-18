import { LinkButton } from "@/components/Button";
import { dictionaries, translate } from "@/i18n";
import { getPreferences } from "@/lib/server/preferences";

export default async function NotFound() {
  const { locale } = await getPreferences();
  const messages = dictionaries[locale];
  return (
    <main id="main" tabIndex={-1} className="layout-container container-narrow app-main">
      <div className="view">
        <h1 tabIndex={-1}>{translate(messages, "notFound.title")}</h1>
        <p>{translate(messages, "notFound.body")}</p>
        <div>
          <LinkButton href="/">{translate(messages, "notFound.cta")}</LinkButton>
        </div>
      </div>
    </main>
  );
}
