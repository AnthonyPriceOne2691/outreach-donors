/**
 * Письма: очередь на отправку с предпросмотром.
 *
 * Единственный экран, где человек смотрит на текст до того, как тот
 * уйдёт постороннему. Отсюда всё его устройство.
 *
 * **Очередь слева, письмо справа.** Список нужен, чтобы выбирать,
 * а решение принимают по тексту — значит текст не прячется за щелчком
 * в отдельное окно. На узком окне столбцы складываются в один.
 *
 * **Отправка — по одному письму.** Кнопки «отправить всё» здесь нет
 * и не будет: она отменяет смысл экрана.
 *
 * **Что мешает отправке, сказано до нажатия, а не после.** Незаполненный
 * юридический блок и ненастоящий транспорт — не мелкий шрифт внизу,
 * а предупреждение рядом с кнопкой, и кнопка при первом из них
 * не нажимается.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Grid,
  Group,
  Loader,
  NumberInput,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { buildLetters, editLetter, listLetters, sendLetter, skipLetter } from '../api/letters';
import type { QueuedLetter } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { LetterPreview, percentOf, toneOf } from './LetterPreview';

const LETTERS_QUERY_KEY = ['letters'] as const;

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

export function LettersPage() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [chosen, setChosen] = useState<number | null>(null);
  const [campaign, setCampaign] = useState('');
  const [limit, setLimit] = useState<number>(50);

  const { data, isLoading, error } = useQuery({
    queryKey: LETTERS_QUERY_KEY,
    queryFn: listLetters,
  });

  // `data?.letters ?? []` в теле создаёт новый массив на каждую отрисовку,
  // и наблюдатель за очередью срабатывал бы всегда. Пустой список — константа.
  const letters = useMemo(() => data?.letters ?? [], [data]);
  const selected = letters.find((letter) => letter.id === chosen) ?? letters[0] ?? null;

  // Выбранное письмо могло уйти из очереди — отправили, пропустили.
  // Тогда выбор снимается, иначе экран показывает письмо, которого
  // в очереди уже нет.
  useEffect(() => {
    if (chosen !== null && !letters.some((letter) => letter.id === chosen)) setChosen(null);
  }, [chosen, letters]);

  const refresh = () => queryClient.invalidateQueries({ queryKey: LETTERS_QUERY_KEY });

  const build = useMutation({
    mutationFn: () => buildLetters({ campaign: campaign.trim(), limit }),
    onSuccess: async () => {
      await refresh();
      notifications.show({
        message: 'Сборка ушла в очередь задач: каждое письмо стоит вызова модели, это минуты',
        color: 'green',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не собрали', message: refusalOf(failure), color: 'red' }),
  });

  const send = useMutation({
    mutationFn: (id: number) => sendLetter(id),
    onSuccess: async (result) => {
      await refresh();
      notifications.show({
        message: result.real
          ? `Письмо ушло с ящика ${result.sender_email}`
          : `Письмо помечено отправленным, но наружу НЕ ушло: транспорт не настроен`,
        color: result.real ? 'green' : 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не отправили', message: refusalOf(failure), color: 'red' }),
  });

  const skip = useMutation({
    mutationFn: (id: number) => skipLetter(id),
    onSuccess: async () => {
      await refresh();
      notifications.show({
        message: 'Письмо убрано из очереди, этот донор в следующей сборке не появится',
        color: 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не убрали', message: refusalOf(failure), color: 'red' }),
  });

  const save = useMutation({
    mutationFn: ({ id, subject, body }: { id: number; subject: string; body: string }) =>
      editLetter(id, { subject, body }),
    onSuccess: async (letter) => {
      await refresh();
      notifications.show({
        message: `Сохранено, отличие от шаблона — ${percentOf(letter.uniqueness)}`,
        color: letter.verdict === null ? 'green' : 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не сохранили', message: refusalOf(failure), color: 'red' }),
  });

  if (isLoading) return <Loader aria-label="Загружаем очередь писем" m="md" />;
  if (error) {
    return (
      <Alert color="red" title="Очередь не загрузилась" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  const view = data!;
  const offCorridor = letters.filter((letter) => letter.verdict !== null).length;
  const busy = send.isPending || skip.isPending || save.isPending;

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <Stack gap={6}>
            <Title order={3}>Письма</Title>
            <Text size="sm" c="dimmed" maw={680}>
              Очередь на отправку. Приветствие, вступление и вопрос переписаны моделью под
              конкретного донора; оффер, условия, подпись и юридический блок неизменны — модель их
              не видит вовсе.
            </Text>
          </Stack>

          <Grid gutter="sm">
            <Grid.Col span={{ base: 6, sm: 3 }}>
              <Metric title="В очереди" value={letters.length} />
            </Grid.Col>
            <Grid.Col span={{ base: 6, sm: 3 }}>
              <Metric
                title="Вне коридора"
                value={offCorridor}
                hint={`коридор ${Math.round(view.corridor.min * 100)}–${Math.round(view.corridor.max * 100)}%`}
                color={offCorridor > 0 ? 'yellow' : undefined}
              />
            </Grid.Col>
            <Grid.Col span={{ base: 6, sm: 3 }}>
              <Metric
                title="Ещё не писали"
                value={view.funnel['ещё не писали'] ?? 0}
                hint={`подходящих ${view.funnel['подходящих'] ?? 0}`}
              />
            </Grid.Col>
            <Grid.Col span={{ base: 6, sm: 3 }}>
              <Metric
                title="Транспорт"
                value={view.transport.real ? view.transport.name : 'нет'}
                hint={view.transport.real ? 'письма уходят' : 'наружу ничего не уходит'}
                color={view.transport.real ? undefined : 'yellow'}
              />
            </Grid.Col>
          </Grid>

          {view.blocked_by.length > 0 ? (
            <Alert color="yellow" title="Отправить нельзя ни одно письмо">
              Не заполнено: {view.blocked_by.join(', ')}. Очередь собирается и видна, но отправка
              откажет: без физического адреса и рабочей отписки рассылка нарушает законы почти во
              всех целевых странах.
            </Alert>
          ) : null}

          {view.transport.problem !== null ? (
            <Alert color="yellow" title="Транспорта нет">
              {view.transport.problem}
            </Alert>
          ) : null}

          {can('send') ? (
            <Group align="flex-end" gap="sm">
              <TextInput
                label="Кампания"
                description="Одноимённая дополняется, а не заводится второй раз"
                placeholder="Май, ниша ремонта"
                value={campaign}
                w={280}
                onChange={(event) => setCampaign(event.currentTarget.value)}
              />
              <NumberInput
                label="Писем за раз"
                description="Каждое стоит вызова модели"
                value={limit}
                min={1}
                max={500}
                w={180}
                onChange={(value) => setLimit(typeof value === 'number' ? value : 50)}
              />
              <Button
                color="lagoon"
                className="press"
                loading={build.isPending}
                disabled={campaign.trim() === ''}
                onClick={() => build.mutate()}
              >
                Собрать очередь
              </Button>
            </Group>
          ) : null}
        </Stack>
      </Card>

      {letters.length === 0 ? (
        <Card className="glass" p="xl">
          <Stack gap="xs">
            <Text fw={500}>Очередь пуста</Text>
            <Text size="sm" c="dimmed">
              Подходящих доноров {view.funnel['подходящих'] ?? 0}, из них с адресом{' '}
              {view.funnel['с адресом'] ?? 0}, и ещё не писали {view.funnel['ещё не писали'] ?? 0}.
              Если последнее число ноль — написаны все; если ноль второе — пора добрать контакты.
            </Text>
          </Stack>
        </Card>
      ) : (
        <Grid gutter="lg" align="flex-start">
          <Grid.Col span={{ base: 12, md: 5 }}>
            <Card className="glass" p="xs">
              <Stack gap={4}>
                {letters.map((letter) => (
                  <LetterRow
                    key={letter.id}
                    letter={letter}
                    active={selected?.id === letter.id}
                    onChoose={() => setChosen(letter.id)}
                  />
                ))}
              </Stack>
            </Card>
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 7 }}>
            {selected !== null ? (
              <LetterPreview
                letter={selected}
                corridor={view.corridor}
                blockedBy={view.blocked_by}
                transportIsReal={view.transport.real}
                canSend={can('send')}
                busy={busy}
                onSend={() => send.mutate(selected.id)}
                onSkip={() => skip.mutate(selected.id)}
                onSave={(subject, body) => save.mutate({ id: selected.id, subject, body })}
              />
            ) : null}
          </Grid.Col>
        </Grid>
      )}
    </Stack>
  );
}

interface RowProps {
  letter: QueuedLetter;
  active: boolean;
  onChoose: () => void;
}

/** Строка очереди. Щёлкают по ней целиком — значит её и поднимаем
 *  под курсором: внутри нет ни одной кнопки, в которую можно промахнуться. */
function LetterRow({ letter, active, onChoose }: RowProps) {
  return (
    <Card
      className={active ? 'glassQuiet press' : 'press liftable'}
      p="sm"
      style={{ cursor: 'pointer' }}
      onClick={onChoose}
      aria-current={active ? 'true' : undefined}
    >
      <Group justify="space-between" wrap="nowrap" gap="sm">
        <Stack gap={2} style={{ minWidth: 0 }}>
          <Text fw={active ? 600 : 500} truncate>
            {letter.host}
          </Text>
          <Text size="xs" c="dimmed" truncate>
            {letter.email ?? 'адрес не определён'}
          </Text>
        </Stack>
        <Badge variant="light" color={toneOf(letter)}>
          {percentOf(letter.uniqueness)}
        </Badge>
      </Group>
    </Card>
  );
}
