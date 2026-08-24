import { Inbox } from "@/components/inbox";

export default async function InboxPage({ searchParams }: { searchParams: Promise<{ mode?: string; location?: string }> }) {
  const { mode, location } = await searchParams;
  return <Inbox initialMode={mode} locationCode={location}/>;
}
