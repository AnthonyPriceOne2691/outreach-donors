/**
 * Переадресация в «Диалогах» (16.11.2026): ответ рекламодателя назвал другой адрес, и то же письмо
 * ушло или уйдёт туда в той же переписке. Слова — сервера (`RedirectCard.words`), экран их не
 * пересказывает: в шапке переписки — строкой под адресом, в списке — меткой с подсказкой.
 *
 * Состояние — меткой той же палитры, что состояние диалога (`api/labels.ts`): серая — ещё в пути,
 * зелёная — переслали, жёлтая — не вышло. Цветной мелкий текст на стекле не держит контраст, метка
 * держит, поэтому слова — обычным цветом, а пока письмо не ушло — приглушённым.
 */

import { Badge, Group, Text, Tooltip } from '@mantine/core';

import { REDIRECT_STEP } from '../api/redirectTypes';
import type { RedirectCard, RedirectState } from '../api/redirectTypes';
import type { LetterCard } from '../api/types';
import { InfoHint } from '../components/InfoHint';

const LABELS: Record<RedirectState, { title: string; color: string }> = {
  checking: { title: 'проверяем', color: 'gray' },
  waiting: { title: 'перешлём', color: 'gray' },
  sent: { title: 'переслали', color: 'green' },
  bounced: { title: 'не дошло', color: 'yellow' },
  stopped: { title: 'не переслали', color: 'yellow' },
  refused: { title: 'не переслали', color: 'yellow' },
};

/** Ещё в пути: решение не окончательное, слова — приглушённые. */
const PENDING: ReadonlySet<string> = new Set<RedirectState>(['checking', 'waiting']);

/** Переадресация жива: письмо на новый адрес — наш ответ на ответ, который этот адрес назвал. */
const ALIVE: ReadonlySet<string> = new Set<RedirectState>(['checking', 'waiting', 'sent']);

/** Отвечает ли письмо на ответ `replyId` (поле ответа под ним тогда закрыто). Письмо переадресации
 *  диалога — только пока она жива: не дошло или снято — переадресация не состоялась, и отвечать
 *  дальше человеку. `answers_reply_id` у снятого письма сервер оставляет, поэтому судит состояние. */
export function answersReply(
  letter: LetterCard,
  replyId: number,
  redirect: RedirectCard | null | undefined,
): boolean {
  if (letter.answers_reply_id !== replyId) return false;
  if (!redirect || letter.step !== REDIRECT_STEP || letter.id !== redirect.letter_id) return true;
  return ALIVE.has(redirect.state);
}

/** Метка состояния; незнакомое слово сервера — им самим, серой меткой. */
export function redirectLabel(state: string): { title: string; color: string } {
  const known: Partial<Record<string, { title: string; color: string }>> = LABELS;
  return known[state] ?? { title: state, color: 'gray' };
}

interface Props {
  redirect: RedirectCard | null | undefined;
}

/** Шапка переписки: метка и слова сервера, пометка об адресе — в «i». */
export function RedirectLine({ redirect }: Props) {
  if (!redirect) return null;
  const label = redirectLabel(redirect.state);
  return (
    <Group gap={6} wrap="nowrap" align="flex-start" data-redirect={redirect.state}>
      <Badge variant="light" color={label.color} size="sm" style={{ flexShrink: 0 }}>
        {label.title}
      </Badge>
      <Text
        size="sm"
        {...(PENDING.has(redirect.state) ? { c: 'dimmed' } : {})}
        style={{ overflowWrap: 'anywhere' }}
      >
        {redirect.words}
      </Text>
      {redirect.note !== null ? (
        <InfoHint name="Пометка к переадресации" width={280}>
          {redirect.note}
        </InfoHint>
      ) : null}
    </Group>
  );
}

/** Строка списка: метка рядом с состоянием диалога, слова сервера — в подсказке. */
export function RedirectBadge({ redirect }: Props) {
  if (!redirect) return null;
  const label = redirectLabel(redirect.state);
  return (
    <Tooltip label={redirect.words} multiline w={280} withArrow>
      <Badge
        variant="light"
        color={label.color}
        className="threadRowRedirect"
        style={{ flexShrink: 0 }}
        aria-label={`Переадресация: ${label.title}`}
      >
        {label.title}
      </Badge>
    </Tooltip>
  );
}
