import { useId, type ElementType, type ReactNode, type Ref } from "react";
import { cx } from "@/lib/cx";

export interface CardProps {
  title?: ReactNode;
  /** Poziom nagłówka karty (domyślnie h2). */
  headingAs?: "h2" | "h3";
  interactive?: boolean;
  /** Ref nagłówka; gdy podany, nagłówek jest fokusowalny programowo (`tabIndex=-1`). */
  headingRef?: Ref<HTMLHeadingElement>;
  className?: string;
  children: ReactNode;
}

export function Card({
  title,
  headingAs = "h2",
  interactive,
  headingRef,
  className,
  children,
}: CardProps) {
  const titleId = useId();
  const Heading: ElementType = headingAs;
  return (
    <section
      className={cx("card", interactive && "card-interactive", className)}
      aria-labelledby={title ? titleId : undefined}
    >
      {title ? (
        <Heading
          className="card__title"
          id={titleId}
          ref={headingRef}
          tabIndex={headingRef ? -1 : undefined}
        >
          {title}
        </Heading>
      ) : null}
      {children}
    </section>
  );
}
