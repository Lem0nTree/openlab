import { ThingDetail } from "@/components/thing-detail";

export default async function ThingPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ThingDetail thingId={id}/>;
}
