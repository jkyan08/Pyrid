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
      <body className="min-h-full flex flex-col bg-white text-gray-900">
        <Providers>
          <header className="border-b border-gray-100">
            <div className="mx-auto flex max-w-4xl items-baseline justify-between gap-4 px-4 py-4">
              <Link href="/" className="text-sm font-semibold tracking-tight text-gray-900">
                Rare Disease Atlas
              </Link>
              <nav className="flex items-baseline gap-4 text-xs text-gray-500">
                <Link href="/mechanism" className="hover:text-gray-900">
                  Mechanism search
                </Link>
              </nav>
            </div>
          </header>
          <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">{children}</main>
          <footer className="border-t border-gray-100">
            <p className="mx-auto max-w-4xl px-4 py-5 text-center text-xs text-gray-400">
              Research navigation, not medical advice.
            </p>
          </footer>
        </Providers>
      </body>
    </html>
  );
}
