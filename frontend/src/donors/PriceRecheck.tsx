/**
 * «Перезапросить цену» на карточке донора (12.10.2026).
 *
 * ТЗ, Этап 2: «свежесть цены 150 дней; старше — сначала перезапрос цены у донора».
 * Протухла ли цена и можно ли перезапросить, решает сервер (`letters/price_recheck.py`):
 * кнопка стоит у любой цены, а окно спрашивает сервер и говорит его словами — цена
 * свежая, перезапросили недавно, донор молчит — или куда и каким текстом уйдёт письмо.
 * Копии срока свежести у экрана нет: она разошлась бы с сервером на первой правке.
 *
 * **Сервер спрашивается, когда окно открыли**: карточку донора читают часто, а перезапрос
 * нужен немногим.
 *
 * **Письмо — целиком и до нажатия**: перезапрос уходит сразу, нажатие и есть согласование.
 * Итог — в том же окне, словами сервера, как у пачки писем (`SendQueue`).
 */

import { Alert, Button, Card, Group, Loader, Modal, Stack, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import type { RecheckCard } from '../api/priceRecheck';
import { fetchRecheck } from '../api/priceRecheck';
import type { DonorFullCard } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatDate } from '../format';
import type { RecheckAsking } from './recheckAsk';
import { useRecheckAsk } from './recheckAsk';
import { RecheckOutcome, Refused, ThreadLink } from './recheckParts';

/** Куда уйдёт письмо и каким текстом — до нажатия. */
function Letter({ card }: { card: RecheckCard }) {
  return (
    <Stack gap={6}>
      {card.way === 'thread' && card.thread_id !== null ? (
        <Text size="sm">
          Уйдёт ответом в <ThreadLink threadId={card.thread_id} /> — на {card.email}, с ящика{' '}
          {card.mailbox}: тем, что ведёт разговор.
        </Text>
      ) : (
        <Text size="sm">
          Переписки нет — уйдёт новым первым письмом на {card.email}, с ящика, свободного сегодня.
        </Text>
      )}
      <Card className="glassQuiet" p="md">
        <Text size="sm" fw={600}>
          {card.subject}
        </Text>
        <Text size="sm" mt={6} style={{ whiteSpace: 'pre-wrap' }}>
          {card.body}
        </Text>
      </Card>
    </Stack>
  );
}

/** Окно перезапроса: что скажет сервер, текст письма и кнопка у того, кому можно. */
function RecheckDialog({
  donorId,
  maySend,
  ask,
}: {
  donorId: number;
  maySend: boolean;
  ask: RecheckAsking;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: recheckKey(donorId),
    queryFn: () => fetchRecheck(donorId),
  });
  if (isLoading) return <Loader size="sm" aria-label="Спрашиваем сервер" />;
  if (error) return <Refused title="Не загрузилось" error={error} />;
  if (data === undefined) return null;
  return (
    <Stack gap="sm">
      <RecheckOutcome done={ask.data} error={ask.error} />
      {data.refusal === null ? (
        <Letter card={data} />
      ) : (
        <Alert color="yellow" title="Перезапрос сейчас не уйдёт">
          {data.refusal}
        </Alert>
      )}
      {data.asked_at !== null ? (
        <Text size="sm" c="dimmed">
          Последний перезапрос ушёл {formatDate(data.asked_at)}.
        </Text>
      ) : null}
      {maySend && data.refusal === null ? (
        <Group>
          <Button className="press" loading={ask.isPending} onClick={() => ask.mutate([donorId])}>
            Отправить перезапрос
          </Button>
        </Group>
      ) : null}
    </Stack>
  );
}

const recheckKey = (donorId: number) => ['price-recheck', donorId] as const;

/** Раздел карточки: у донора с ценой — кнопка к окну перезапроса. Цены нет — раздела нет:
 *  перезапрашивать нечего. Нажатие живёт выше окна (`recheckAsk.ts`): закрыли окно, пока
 *  письмо уходит, — открыли снова, и кнопка заперта до его итога. */
export function PriceRecheck({ donor }: { donor: DonorFullCard }) {
  const { can } = useSession();
  const [opened, setOpened] = useState(false);
  const ask = useRecheckAsk([recheckKey(donor.id), ['donor', String(donor.id)]]);
  if (donor.last_price === null) return null;
  const maySend = can('send');
  return (
    <Card className="glassPanel" p="xl" mt="lg">
      <Stack gap={6}>
        <Group justify="space-between" gap="sm">
          <Title order={4}>Перезапрос цены</Title>
          <Button
            variant="light"
            className="press"
            aria-haspopup="dialog"
            onClick={() => setOpened(true)}
          >
            {maySend ? 'Перезапросить цену' : 'Можно ли перезапросить'}
          </Button>
        </Group>
        {/* Слова — для любой цены: протухла ли она, говорит сервер в окне (проверка
            12.10.2026: «цена старше срока» стояло и у свежей). */}
        <Text size="sm" c="dimmed">
          Цена старше срока свежести останавливает Этап 2 по донору, пока он её не подтвердит или не
          назовёт новую. Перезапрос — короткое письмо донору; можно ли отправить его сейчас, скажет
          окно.
        </Text>
      </Stack>
      <Modal
        opened={opened}
        onClose={() => setOpened(false)}
        title={`Перезапрос цены · ${donor.host}`}
        size="lg"
      >
        {opened ? <RecheckDialog donorId={donor.id} maySend={maySend} ask={ask} /> : null}
      </Modal>
    </Card>
  );
}
