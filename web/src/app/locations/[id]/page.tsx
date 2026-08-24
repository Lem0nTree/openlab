import { LocationDetail } from "@/components/location-detail";

export default async function LocationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <LocationDetail locationId={id}/>;
}
