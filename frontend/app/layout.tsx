import type { Metadata } from "next";
import "./globals.css";
import "./tokens.css";
import { Analytics } from "@vercel/analytics/next";
import { AuthProvider } from "@/lib/auth-context";
import GuestGate from "@/components/GuestGate";
import { Inter, Space_Grotesk } from "next/font/google";

// Body/UI face — highly legible variable sans (consumed via --font-sans).
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
// Distinguished display face — modern geometric with character, for the
// wordmark + headings (font-display utility). Pairs with Inter for body.
const displayFace = Space_Grotesk({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-display-face",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Curio",
  description: "Educational short-form video, tailored to what you want to learn.",
  appleWebApp: {
    capable: true,
    title: "Curio",
    statusBarStyle: "default",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${displayFace.variable} bg-black text-white antialiased`}>
        <AuthProvider>
          {children}
          <GuestGate />
        </AuthProvider>
        <Analytics />
      </body>
    </html>
  );
}
