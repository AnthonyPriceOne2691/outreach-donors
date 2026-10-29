/**
 * «Доставка откладывается» — письма, которые ушли, а сервер получателя их пока не принимает
 * (`letters/deferrals.py`, 29.10.2026).
 *
 * Платформа повторяет доставку до 72 часов, и исход придёт сам — доставкой или отказом. До этого
 * такое письмо было просто «отправлено»: отсрочки отбрасывались, о беде говорила только тревога
 * через шесть часов, а причину искали в журнале платформы. Теперь у каждого письма — причина
 * словами сервера, попытки и часы (`deliveryLine.ts`), а вся история — в его переписке.
 *
 * **Блока нет, пока откладываемых нет**, как у «Исход неизвестен»: пустая карточка над очередью
 * читалась бы как неполадка. Не загрузился — отказ словами. **Только чтение**: решать здесь
 * нечего — исход придёт сам, а что делать с причиной, видно из её слов.
 */

import { Alert, Card, Stack, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import type { DeferredLetter } from '../api/delivery';
import { listDeferredLetters } from '../api/letters';
import type { LetterAudience } from '../api/letters';
import type { LetterStage } from '../api/types';
import { Seams } from '../components/Seams';
import { capitalized, formatNumber } from '../format';
import { attemptsLine } from './deliveryLine';
import { ThreadLink } from './ThreadLink';

/** Раз в минуту: отсрочки идут весь день, и открытый экран видит новые без перезагрузки. */
const REFRESH_MS = 60_000;

interface Props {
  stage: LetterStage;
  /** Аудитория Этапа 2; нет — по найденной ссылке. */
  audience?: LetterAudience;
}

export function DeferredLetters({ stage, audience = 'links' }: Props) {
  const { data, error } = useQuery({
    queryKey: ['letters', 'deferred', stage, audience],
    queryFn: () => listDeferredLetters(stage, audience),
    refetchInterval: REFRESH_MS,
  });

  if (data === undefined) {
    return error ? (
      <Alert color="red" title="Письма с отложенной доставкой не загрузились">
        {refusalOf(error)}
      </Alert>
    ) : null;
  }
  if (data.letters.length === 0) return null;
  const more = data.total - data.letters.length;

  return (
    <Card className="glassPanel" p="xl">
      <Stack gap="sm">
        <Title order={4}>Доставка откладывается · {formatNumber(data.total)}</Title>
        <Text size="sm" c="dimmed" maw={720}>
          Эти письма ушли, а сервер получателя их пока не принимает: платформа повторяет доставку до
          72 часов, и исход придёт сам — доставкой или отказом. Почему откладывается — у каждого
          письма словами сервера.
        </Text>
        {data.letters.map((letter) => (
          <DeferredRow key={letter.id} letter={letter} />
        ))}
        {more > 0 ? (
          <Text size="sm" c="dimmed">
            И ещё {formatNumber(more)} — выше те, что откладываются дольше.
          </Text>
        ) : null}
      </Stack>
    </Card>
  );
}

/** Письмо: сайт, причина словами с попытками и часами, кому и из какой рассылки. */
function DeferredRow({ letter }: { letter: DeferredLetter }) {
  return (
    <Card className="glassQuiet" p="sm">
      <Stack gap={2} style={{ minWidth: 0 }}>
        {/* Длинный домен переносится по своим швам, а не посреди слова. */}
        <Text fw={500} className="cellName">
          <Seams text={letter.host} />
        </Text>
        <Text size="sm">
          {capitalized(letter.deferral.words)} · {attemptsLine(letter.deferral)}
        </Text>
        <Text size="sm" c="dimmed">
          Кому {letter.email ?? 'адрес не определён'} · {letter.campaign}
        </Text>
        <ThreadLink threadId={letter.thread_id} />
      </Stack>
    </Card>
  );
}
