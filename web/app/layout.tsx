import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { Providers } from "@/components/providers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Rare Disease Atlas",
  description:
    "Research navigation across rare-disease mechanisms, evidence and communities. Not medical advice.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-[#f8fafb] text-gray-900">
        <Providers>
          <header className="border-b border-slate-200 bg-white/90">
            <div className="mx-auto flex max-w-[1600px] items-center justify-between gap-4 px-5 py-4 sm:px-8">
              <Link href="/" className="flex items-center gap-3 text-sm font-semibold tracking-tight text-slate-950">
                <span className="flex size-8 items-center justify-center rounded-lg bg-slate-950 text-base text-white">✳</span>
                Rare Disease Atlas
              </Link>
              <nav className="flex items-center gap-4 text-xs font-medium text-slate-600 sm:text-sm">
                <Link href="/" className="hover:text-slate-950">Explore</Link>
                <Link href="/mechanism" className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1.5 text-amber-950 transition-colors hover:bg-amber-100">
                  Mechanism finder
                </Link>
              </nav>
            </div>
          </header>
          <main className="mx-auto w-full max-w-[1600px] flex-1 px-5 py-7 sm:px-8 lg:py-9">{children}</main>
          <footer className="border-t border-gray-100">
            <p className="mx-auto max-w-[1600px] px-5 py-5 text-center text-xs text-gray-400">
              Research navigation, not medical advice.
            </p>
          </footer>
        </Providers>
      </body>
    </html>
  );
}
