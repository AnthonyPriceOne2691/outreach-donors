/**
 * «Исход неизвестен» — письма, зависшие в «отправляется» (`letters/unknown_outcome.py`).
 *
 * Связь с почтой оборвалась посреди передачи: платформа могла принять письмо,
 * а могла и нет. Ключа повтора у неё нет, и повтор вслепую — второе письмо тому
 * же человеку, поэтому сами такие письма не повторяются. Сообщит о письме
 * платформа — исход решит её событие; нет — через пять минут письмо встаёт
 * сюда, и решает человек по журналу платформы.
 *
 * **Блока нет, пока решать нечего.** Пустой список — обычное состояние,
 * и пустая карточка над очередью читалась бы как неполадка. Не загрузился —
 * отказ словами: пропавший молча блок читался бы как «зависших нет».
 *
 * **Решение — через окно, которое говорит, чем оно кончится.** «Ушло» пускает
 * срок добивки от начала передачи; «Вернуть в очередь» отправит письмо снова,
 * и если оно всё же ушло, адресат получит его второй раз.
 *
 * **Без права на отправку список виден, кнопок нет**: решение — это разрешение
 * отправить письмо, которое, возможно, уже ушло.
 *
 * **Список — вкладки** (этап и аудитория): письмо бизнеса ниши, на котором встала
 * его пачка, решают на вкладке «Бизнесам ниши», а не среди писем по ссылке.
 */

import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Modal,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { listUnknownLetters, resolveLetter } from '../api/letters';
import type { LetterAudience } from '../api/letters';
import type { LetterStage } from '../api/types';
import type { ResolveOutcome, UnknownLetter } from '../api/unknownOutcome';
import { Seams } from '../components/Seams';
import { formatDateTime, formatNumber } from '../format';
import { notify } from '../notices';

/** Раз в минуту: письмо встаёт сюда спустя минуты после обрыва, и экран,
 *  открытый всё это время, должен показать его без перезагрузки. */
const REFRESH_MS = 60_000;

interface Choice {
  letter: UnknownLetter;
  outcome: ResolveOutcome;
}

interface Props {
  stage: LetterStage;
  /** Аудитория Этапа 2; нет — по найденной ссылке, как до аудиторий. */
  audience?: LetterAudience;
  canSend: boolean;
}

export function UnknownOutcome({ stage, audience = 'links', canSend }: Props) {
  const queryClient = useQueryClient();
  const [choice, setChoice] = useState<Choice | null>(null);
  const { data, error } = useQuery({
    queryKey: ['letters', 'unknown', stage, audience],
    queryFn: () => listUnknownLetters(stage, audience),
    refetchInterval: REFRESH_MS,
  });

  const resolve = useMutation({
    mutationFn: ({ letter, outcome }: Choice) => resolveLetter(letter.id, outcome),
    onSuccess: (done, { letter }) =>
      notify({
        message: `${letter.host}: ${done.said}`,
        color: done.status === 'sent' ? 'green' : 'yellow',
      }),
    onError: (failure) => notify({ title: 'Не решили', message: refusalOf(failure), color: 'red' }),
    // Решение меняет и этот список, и очередь: вернувшееся письмо встаёт в неё.
    // Отказ — тоже повод освежить: письмо секундой раньше решило событие платформы.
    onSettled: async () => {
      setChoice(null);
      await queryClient.invalidateQueries({ queryKey: ['letters'] });
    },
  });

  if (data === undefined) {
    return error ? (
      <Alert color="red" title="Письма с неизвестным исходом не загрузились">
        {refusalOf(error)}
      </Alert>
    ) : null;
  }
  if (data.letters.length === 0) return null;

  return (
    <Card className="glassPanel" p="xl">
      <Stack gap="sm">
        <Title order={4}>Исход неизвестен · {formatNumber(data.letters.length)}</Title>
        <Text size="sm" c="dimmed" maw={720}>
          Эти письма отдали почте больше {formatNumber(data.after_minutes)} минут назад, и связь
          оборвалась посреди передачи: ушли они или нет, неизвестно — платформа о них не сообщила.
          Сами они не повторяются: повтор мог бы отправить письмо второй раз. Найдите письмо в
          журнале платформы по адресу и времени: нашлось — «Ушло», нет — «Вернуть в очередь».
        </Text>
        {canSend ? null : (
          <Text size="sm" c="dimmed">
            Решает человек с правом на отправку: решение — это разрешение отправить письмо, которое,
            возможно, уже ушло.
          </Text>
        )}
        {data.letters.map((letter) => (
          <StuckLetter
            key={letter.id}
            letter={letter}
            canSend={canSend}
            busy={resolve.isPending}
            onChoose={(outcome) => setChoice({ letter, outcome })}
          />
        ))}
      </Stack>
      <Decision
        choice={choice}
        busy={resolve.isPending}
        onClose={() => setChoice(null)}
        onConfirm={() => choice && resolve.mutate(choice)}
      />
    </Card>
  );
}

interface LetterProps {
  letter: UnknownLetter;
  canSend: boolean;
  busy: boolean;
  onChoose: (outcome: ResolveOutcome) => void;
}

/** Письмо и то, по чему его ищут в журнале платформы: кому, с какого ящика, когда. */
function StuckLetter({ letter, canSend, busy, onChoose }: LetterProps) {
  return (
    <Card className="glassQuiet" p="sm">
      <Group justify="space-between" align="flex-start" gap="sm">
        <Stack gap={2} style={{ minWidth: 0 }}>
          <Group gap="xs">
            {/* Длинный домен переносится по своим швам, а не посреди слова. */}
            <Text fw={500} className="cellName">
              <Seams text={letter.host} />
            </Text>
            <Badge variant="light" color="yellow">
              {letter.what}
            </Badge>
          </Group>
          <Text size="sm" c="dimmed">
            Кому {letter.email ?? 'адрес не определён'}, с ящика {letter.sender_email ?? '—'},
            отдано почте {formatDateTime(letter.since)} · {letter.campaign}
          </Text>
          {/* Чернилами и с подчёркиванием, как ссылки посреди текста на главной:
              бирюзовая на светлом месте стекла намерилась 4,23 : 1 (07.10.2026). */}
          {letter.thread_id === null ? null : (
            <Anchor
              component={Link}
              to={`/threads/${letter.thread_id}`}
              size="sm"
              fw={500}
              c="var(--ink)"
              underline="always"
              className="inkLink"
            >
              Переписка
            </Anchor>
          )}
        </Stack>
        {canSend ? (
          <Group gap="xs">
            <Button
              size="xs"
              variant="light"
              className="press"
              disabled={busy}
              onClick={() => onChoose('sent')}
            >
              Ушло
            </Button>
            <Button
              size="xs"
              variant="default"
              className="press"
              disabled={busy}
              onClick={() => onChoose('queued')}
            >
              Вернуть в очередь
            </Button>
          </Group>
        ) : null}
      </Group>
    </Card>
  );
}

interface DecisionProps {
  choice: Choice | null;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

/** Окно решения: чем оно кончится — до нажатия, а не после. */
function Decision({ choice, busy, onClose, onConfirm }: DecisionProps) {
  const sent = choice?.outcome === 'sent';
  const host = choice?.letter.host ?? '';
  return (
    <Modal
      opened={choice !== null}
      onClose={onClose}
      title={sent ? `Письмо ${host} ушло?` : `Вернуть письмо ${host} в очередь?`}
    >
      {choice === null ? null : (
        <Stack gap="sm">
          {sent ? (
            <Text size="sm">
              Отмечайте, только если нашли письмо в журнале платформы. Оно станет «принято
              платформой» с минуты передачи — {formatDateTime(choice.letter.since)}: от неё пойдут
              срок добивки и дневной лимит ящика.
            </Text>
          ) : (
            <>
              <Alert color="red" title="Если оно всё же ушло, адресат получит его второй раз">
                Возвращайте, только если письма нет в журнале платформы.
              </Alert>
              <Text size="sm">
                Письмо снова будет ждать отправки: первое — в очереди здесь, добивка — в своей
                цепочке, ответ — в карточке переписки. Добивка и ответ уйдут только с ящика своей
                переписки.
              </Text>
            </>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              Отмена
            </Button>
            <Button color={sent ? 'lagoon' : 'red'} loading={busy} onClick={onConfirm}>
              {sent ? 'Ушло' : 'Вернуть в очередь'}
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
