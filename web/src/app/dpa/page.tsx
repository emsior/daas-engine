import { readFile } from "node:fs/promises";
import path from "node:path";
import { LinkButton } from "@/components/Button";
import { dictionaries, translate } from "@/i18n";
import { renderMarkdown } from "@/lib/markdown";
import { getPreferences } from "@/lib/server/preferences";

export const metadata = { title: "DPA" };

/** Treść DPA v1 z pliku Markdown (placeholder do przeglądu prawnego), renderowana jako JSX. */
export default async function DpaPage() {
  const { locale } = await getPreferences();
  const messages = dictionaries[locale];
  const source = await readFile(path.join(process.cwd(), "src", "content", "dpa_v1_pl.md"), "utf8");

  return (
    <div className="view">
      <div className="stack-2">
        <h1 tabIndex={-1}>{translate(messages, "dpa.title")}</h1>
        <p className="text-muted">{translate(messages, "dpa.draft")}</p>
      </div>
      <article className="dpa-text" lang="pl">
        {renderMarkdown(source)}
      </article>
      <div>
        <LinkButton href="/" variant="secondary">
          {translate(messages, "dpa.back")}
        </LinkButton>
      </div>
    </div>
  );
}
