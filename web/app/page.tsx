import Link from "next/link";
import { SearchBox } from "@/components/search-box";

export default function HomePage() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-12 pt-10 sm:pt-20">
      <div className="space-y-4 text-center">
        <h1 className="text-3xl font-semibold tracking-tight text-gray-900 sm:text-4xl">
          Find the mechanism behind the name
        </h1>
        <p className="mx-auto max-w-2xl text-base leading-relaxed text-gray-500">
          One search across diseases, genes, variants, symptoms, patient groups and mechanisms.
          Every connection is explained and cited; where no supported route exists, the atlas
          says so honestly.
        </p>
      </div>

      <SearchBox autoFocus hero />

      <p className="text-center text-sm text-gray-500">
        I have a mechanism, not a diagnosis —{" "}
        <Link
          href="/mechanism"
          className="text-sky-700 underline decoration-sky-300 underline-offset-2 hover:text-sky-900"
        >
          search by mechanism direction
        </Link>
        .
      </p>
    </div>
  );
}
