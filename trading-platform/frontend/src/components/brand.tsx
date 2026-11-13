import { APP_NAME } from "@/lib/config";

export function Brand({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-semibold tracking-tight ${className}`}>
      <span aria-hidden className="grid size-6 place-items-center rounded-md bg-primary text-primary-foreground">
        <svg viewBox="0 0 16 16" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M2 11l4-4 3 3 5-6" />
        </svg>
      </span>
      {APP_NAME}
    </span>
  );
}
