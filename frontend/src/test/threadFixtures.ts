/**
 * Переписка для тестов файлов: наше первое письмо и ответ человека с прайсом.
 * Данные выдуманные — домены в зоне `.example.test`.
 */

import type { FileRules, OutgoingFile, ReplyFile } from '../api/files';
import type { ThreadView } from '../api/thread';
import type { IncomingCard, LetterCard } from '../api/types';

/** Правила файла, как их называет сервер (`letters/outgoing_files`). */
export const FILE_RULES: FileRules = {
  max_files: 5,
  max_file_bytes: 7_000_000,
  max_letter_bytes: 7_000_000,
  extensions: ['pdf', 'docx', 'xlsx', 'csv', 'txt', 'png', 'jpg', 'jpeg'],
  pending_days: 7,
};

export const PRICE_FILE: ReplyFile = {
  id: 5,
  name: 'price-2026.xlsx',
  size: 18_400,
  content_type: 'application/vnd.ms-excel',
  accepted: true,
  reason: null,
  has_text: true,
  text_note: 'скрытый лист «tmp» пропущен',
};

export const OUR_FILE: OutgoingFile = { id: 31, name: 'media-kit.pdf', size: 240_000 };

export function letter(extra: Partial<LetterCard> = {}): LetterCard {
  return {
    answers_reply_id: null,
    body: 'Hello, could you share your rates?',
    id: 41,
    sent_at: '2026-10-08T11:00:00+00:00',
    status: 'delivered',
    step: 0,
    subject: 'Rates',
    uniqueness: null,
    ...extra,
  };
}

export function reply(extra: Partial<IncomingCard> = {}): IncomingCard {
  return {
    attachments: [PRICE_FILE],
    confidence: 0.92,
    currency: 'USD',
    from_email: 'sales@prices.example.test',
    id: 52,
    kind: 'human',
    lead: false,
    needs_review: false,
    payment_methods: null,
    placement: 'sells',
    price_grey: null,
    price_white: '300',
    raw_body: 'Prices are in the attached file.',
    received_at: '2026-10-08T12:00:00+00:00',
    reviewed_at: null,
    reviewed_by: null,
    subject: 'Re: Rates',
    ...extra,
  };
}

export function threadWith(extra: Partial<ThreadView> = {}): ThreadView {
  return {
    card: {
      campaign: 'Файлы',
      contact_email: 'sales@prices.example.test',
      currency: null,
      host: 'prices.example.test',
      id: 8,
      last_event_at: null,
      last_reply_at: null,
      messages_sent: 1,
      price_grey: null,
      price_white: null,
      stage: 'donors',
      state: 'priced',
    },
    corridor: { min: 0.15, max: 0.25 },
    file_rules: FILE_RULES,
    incoming: [reply()],
    letters: [letter()],
    mail: null,
    pending_files: [],
    ...extra,
  };
}
