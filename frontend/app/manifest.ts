import type { MetadataRoute } from "next";

// Web app manifest — lets users "Add to Home Screen" and launch Curio
// full-screen like a native app (no browser chrome).
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Curio",
    short_name: "Curio",
    description: "Learn anything as a feed of short videos.",
    start_url: "/",
    display: "standalone",
    background_color: "#FBF7ED",
    theme_color: "#FBF7ED",
  };
}
