import { formatDateTime, formatDateTimeShort } from "@/lib/format";

/** A timestamp: full with time zone on wide screens, compact on phones. */
export function When({ iso, timeZone }: { iso: string | null | undefined; timeZone?: string }) {
  return (
    <time dateTime={iso ?? undefined} title={formatDateTime(iso, timeZone)}>
      <span className="sm:hidden">{formatDateTimeShort(iso, timeZone)}</span>
      <span className="hidden sm:inline">{formatDateTime(iso, timeZone)}</span>
    </time>
  );
}
