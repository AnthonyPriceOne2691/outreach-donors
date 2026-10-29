/**
 * Журнал действий (`GET /journal`, сервер — `access/journal.py`): кто, что, когда и над чем.
 *
 * Читает тот, кто заводит учётки (право `users`). Без права «Продажи» сервер отдаёт журнал
 * без действий этапа продаж и говорит об этом (`sales_hidden`) — экран называет это словами.
 */

import { request } from './client';

/** Что попадает в журнал — значения сервера (`AuditAction`). Слова — `auditLabels.ts`,
 *  сверку с сервером держит тест сервера (`tests/test_journal_words.py`). */
export type AuditAction =
  | 'login'
  | 'login_failed'
  | 'password_changed'
  | 'user_created'
  | 'user_updated'
  | 'run_started'
  | 'thresholds_changed'
  | 'letter_sent'
  | 'price_reviewed'
  | 'contact_added'
  | 'contact_removed'
  | 'advertiser_reviewed'
  | 'site_reviewed'
  | 'suppression_added'
  | 'suppression_removed'
  | 'lead_taken'
  | 'sales_leads_imported'
  | 'agent_settings_changed'
  | 'sales_kb_changed'
  | 'data_pruned'
  | 'probe_created'
  | 'sales_chain_changed'
  | 'agent_draft_decided'
  | 'ahrefs_cap_changed'
  | 'llm_cap_changed'
  | 'crawl_backlog_dropped';

export interface JournalEntry {
  id: number;
  created_at: string;
  /** Код действия. Сервер новее экрана — придёт незнакомый: экран покажет его словами
   *  «другое действие», а не упадёт. */
  action: string;
  /** Кто. `null` — автора нет: вход с чужой почтой, загрузка из консоли. */
  author_id: number | null;
  author_email: string | null;
  /** Над чем: «run:27», «donor:12», «user:3». */
  target: string | null;
  /** Подробности как записаны: ключи словами, значения — как были. */
  details: Record<string, unknown> | null;
}

export interface JournalView {
  entries: JournalEntry[];
  total: number;
  page: number;
  limit: number;
  /** Действия этапа продаж не показаны: у учётки нет права «Продажи». */
  sales_hidden: boolean;
}

export interface JournalQuery {
  page: number;
  action: AuditAction | null;
  author: number | null;
  /** Моменты с поясом: начало первого дня и начало дня после последнего — по часам
   *  человека (`journalFilters.momentsOf`). */
  since: string | null;
  until: string | null;
}

export function fetchJournal(query: JournalQuery): Promise<JournalView> {
  const params = new URLSearchParams({ page: String(query.page) });
  if (query.action !== null) params.set('action', query.action);
  if (query.author !== null) params.set('author', String(query.author));
  if (query.since !== null) params.set('since', query.since);
  if (query.until !== null) params.set('until', query.until);
  return request<JournalView>(`/journal?${params.toString()}`);
}
