import type { Metadata } from "next";
import LegalLayout, { LegalSection } from "@/components/LegalLayout";

export const metadata: Metadata = {
  title: "Content Policy · Curio",
  description: "How Curio sources content and handles takedown requests.",
};

// Generated starting draft — review with counsel before relying on it. The
// copyright/DMCA section in particular should be confirmed against your real
// process and, in the US, a registered DMCA agent.
export default function ContentPolicyPage() {
  return (
    <LegalLayout title="Content Policy" lastUpdated="June 20, 2026">
      <p>
        Curio links to and embeds short educational segments from third-party
        platforms. We do not host, store, download, or rebroadcast the
        underlying media — we store only references (a source link plus start and
        end timestamps) and metadata we derive to organize the feed. This policy
        explains how content is sourced and how to report a concern.
      </p>

      <LegalSection heading="1. How content is sourced">
        <p>
          Curio identifies relevant segments from publicly available videos on
          third-party platforms and presents them through those platforms&apos;
          official embed players. Playback always occurs within the source
          platform&apos;s player, subject to its terms, and the source platform
          continues to serve the media, attribution, and any advertising. Curio
          never strips attribution or advertising and never re-hosts media.
        </p>
      </LegalSection>

      <LegalSection heading="2. Respecting source platforms">
        <p>
          We use only official, publicly available embed mechanisms and operate
          in line with source platforms&apos; terms and technical limits. We do
          not download, cache, or redistribute media, and we do not attempt to
          bypass access controls, paywalls, or geo-restrictions imposed by a
          source platform.
        </p>
      </LegalSection>

      <LegalSection heading="3. Copyright & takedown requests">
        <p>
          We respect intellectual property rights. If you believe content
          surfaced through Curio infringes your copyright, send a notice to the
          address below including: (a) identification of the work; (b) the link
          or clip at issue; (c) your contact information; (d) a statement that
          you have a good-faith belief the use is unauthorized; and (e) a
          statement, under penalty of perjury, that the information is accurate
          and that you are the rights holder or authorized to act on their
          behalf.
        </p>
        <p>
          On receipt of a valid notice we will remove or disable access to the
          referenced clip reference promptly. Because Curio does not host the
          media, removing a clip from Curio removes our reference to it; the
          underlying video remains controlled by the source platform.
        </p>
        <p>
          Copyright contact:{" "}
          <a href="mailto:copyright@curio.app" className="underline font-bold">copyright@curio.app</a>.
          [In the US, register a designated DMCA agent and confirm your
          safe-harbor process with counsel.]
        </p>
      </LegalSection>

      <LegalSection heading="4. Prohibited content">
        <p>
          Content surfaced or submitted through Curio must not be unlawful,
          infringing, hateful, harassing, sexually explicit, deceptive, or
          otherwise harmful, consistent with the acceptable-use rules in our{" "}
          <a href="/terms" className="underline font-bold">Terms of Service</a>.
        </p>
      </LegalSection>

      <LegalSection heading="5. Reporting & moderation">
        <p>
          You can report content you believe violates this policy at{" "}
          <a href="mailto:report@curio.app" className="underline font-bold">report@curio.app</a>.
          We review reports and may remove references, restrict features, or take
          other appropriate action. If you believe content was removed in error,
          you may contact us to request a review.
        </p>
      </LegalSection>

      <p className="text-ink/50 text-xs pt-2">
        This document is provided for general informational purposes and is not
        legal advice.
      </p>
    </LegalLayout>
  );
}
