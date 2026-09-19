/**
 * Переписка целиком.
 *
 * Письма идут подряд, как в мессенджере. **Рядом с ответом — то, что
 * из него распознали: цена белая, цена серая, способы оплаты. Именно
 * рядом, а не вместо**: человек должен видеть, откуда взялось число,
 * иначе проверить его нечем.
 *
 * Отметки доставки — одна галочка и две: принято платформой и доставлено
 * на сервер получателя. Прочитано не показываем: отслеживание открытий
 * требует картинки-маячка, а она сама по себе повод уйти в спам.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Divider,
  Group,
  Loader,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { IconArrowLeft, IconCheck, IconChecks } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';

import { MESSAGE_STATUSES, REPLY_KINDS, THREAD_STATES } from '../api/labels';
import { fetchThread } from '../api/outreach';
import type { IncomingCard, LetterCard, MessageStatus } from '../api/types';

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

function when(moment: string | null): string {
  return moment === null ? '—' : new Date(moment).toLocaleString('ru-RU');
}

/** Одна галочка — принято платформой, две — доставлено получателю. */
function DeliveryMark({ status }: { status: MessageStatus }) {
  if (status === 'delivered') {
    return <IconChecks size={16} aria-label="доставлено" />;
  }
  if (status === 'sent') {
    return <IconCheck size={16} aria-label="принято платформой" />;
  }
  return null;
}

function Letter({ letter }: { letter: LetterCard }) {
  return (
    <Card className="glassQuiet" p="md" ml={0} mr="15%">
      <Group justify="space-between" gap="xs" mb="xs">
        <Group gap="xs">
          <Badge variant="light" color="lagoon">
            {letter.step === 0 ? 'первое письмо' : `добивка ${letter.step}`}
          </Badge>
          <Text size="xs" c="dimmed">
            {MESSAGE_STATUSES[letter.status]}
          </Text>
          <DeliveryMark status={letter.status} />
        </Group>
        <Text size="xs" c="dimmed">
          {when(letter.sent_at)}
        </Text>
      </Group>
      {letter.subject !== null && (
        <Text fw={500} mb={4}>
          {letter.subject}
        </Text>
      )}
      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
        {letter.body}
      </Text>
      {letter.uniqueness_pct !== null && (
        <Text size="xs" c="dimmed" mt="xs">
          Отличие от шаблона {letter.uniqueness_pct.toFixed(0)}% — цель 15–25%
        </Text>
      )}
    </Card>
  );
}

function Incoming({ incoming }: { incoming: IncomingCard }) {
  const kind = REPLY_KINDS[incoming.kind];
  const hasPrice = incoming.price_white !== null || incoming.price_grey !== null;

  return (
    <Card className="glass" p="md" ml="15%" mr={0}>
      <Group justify="space-between" gap="xs" mb="xs">
        <Badge variant="light" color={kind.color}>
          {kind.title}
        </Badge>
        <Text size="xs" c="dimmed">
          {when(incoming.received_at)}
        </Text>
      </Group>

      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
        {incoming.raw_body}
      </Text>

      {hasPrice && (
        <>
          <Divider my="sm" variant="dashed" />
          {/* Распознанное — рядом с исходным текстом, а не вместо него:
              иначе спорный разбор нечем проверить. */}
          <Group gap="sm">
            {incoming.price_white !== null && (
              <Badge variant="light" color="green">
                белая {incoming.price_white} {incoming.currency}
              </Badge>
            )}
            {incoming.price_grey !== null && (
              <Badge variant="light" color="yellow">
                серая {incoming.price_grey} {incoming.currency}
              </Badge>
            )}
            {(incoming.payment_methods ?? []).map((method) => (
              <Badge key={method} variant="light" color="gray">
                {method}
              </Badge>
            ))}
            {incoming.confidence !== null && (
              <Text size="xs" c="dimmed">
                уверенность разбора {(incoming.confidence * 100).toFixed(0)}%
              </Text>
            )}
          </Group>
        </>
      )}
    </Card>
  );
}

export function ThreadPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ['thread', id],
    queryFn: () => fetchThread(Number(id)),
    enabled: id !== undefined,
  });

  if (isLoading) return <Loader aria-label="Загружаем переписку" m="md" />;
  if (error) {
    return (
      <Alert color="red" title="Переписка не загрузилась" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }
  if (data === undefined) return null;

  // Письма и входящие идут одной лентой по времени — так же, как их
  // читал бы человек в почте.
  const timeline = [
    ...data.letters.map((letter) => ({
      at: letter.sent_at ?? '',
      node: <Letter key={`letter-${letter.id}`} letter={letter} />,
    })),
    ...data.incoming.map((incoming) => ({
      at: incoming.received_at,
      node: <Incoming key={`incoming-${incoming.id}`} incoming={incoming} />,
    })),
  ].sort((a, b) => a.at.localeCompare(b.at));

  return (
    <Stack gap="lg" maw={900}>
      <Card className="glassPanel" p="xl">
        <Group justify="space-between" align="flex-start">
          <Stack gap={6}>
            <Group gap="sm">
              <Title order={3}>{data.card.host}</Title>
              <Badge variant="light" color={THREAD_STATES[data.card.state].color}>
                {THREAD_STATES[data.card.state].title}
              </Badge>
            </Group>
            <Text size="sm" c="dimmed">
              {data.card.contact_email ?? 'адрес не определён'} · кампания «{data.card.campaign}»
            </Text>
          </Stack>
          <Button
            variant="subtle"
            className="press"
            leftSection={<IconArrowLeft size={16} />}
            onClick={() => void navigate('/threads')}
          >
            К списку
          </Button>
        </Group>
      </Card>

      <Stack gap="md">{timeline.map((item) => item.node)}</Stack>

      <Card className="glass" p="md">
        <Text size="sm" c="dimmed">
          Ответить прямо отсюда можно будет, когда появится право на отправку и первые почтовые
          домены: ответ уходит от того же отправителя, что вёл переписку — менять ящик на середине
          разговора значит попасть в спам.
        </Text>
      </Card>
    </Stack>
  );
}
