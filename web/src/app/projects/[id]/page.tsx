import { BuildDetail } from "@/components/build-detail";

export default async function BuildDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <BuildDetail projectId={id}/>;
}
