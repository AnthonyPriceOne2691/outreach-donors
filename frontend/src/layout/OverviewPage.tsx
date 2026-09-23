/**
 * Обзор: кто вошёл, что молчит, из чего состоит работа.
 *
 * Пустая главная с надписью «добро пожаловать» хуже, чем короткая карта
 * сервиса: человек иначе ищет нужный раздел глазами. И отдельной строкой —
 * что отправка писем подключается на рабочем сервере: без неё очередь
 * писем, которая никуда не уходит, читается как поломка.
 */

import {
  Alert,
  Anchor,
  Badge,
  Card,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { fetchWatchdog } from '../api/settings';
import { permissionTitle } from '../api/labels';
import type { Permission } from '../api/types';
import { useSession } from '../auth/AuthProvider';

/**
 * Сторож тишины на главной, а не в отдельном разделе: поломка этого
 * класса не показывает себя нигде, и человек не пойдёт её искать —
 * он увидит, что «всё тихо», и закроет вкладку.
 */
function Watchdog() {
  const { can } = useSession();
  const { data, isLoading } = useQuery({
    queryKey: ['watchdog'],
    queryFn: fetchWatchdog,
    enabled: can('view'),
    // Тревоги меняются часами, а не секундами: спрашивать чаще незачем.
    refetchInterval: 5 * 60 * 1000,
  });

  if (!can('view')) return null;

  const alarms = data?.alarms ?? [];
  // Ожидание — внутри карточки, а не одиноким кружком над ней.
  if (isLoading || alarms.length === 0) {
    return (
      <Card className="glass" p="lg">
        <Group gap="xs" mb={4}>
          <Title order={5}>Сторож тишины</Title>
          {isLoading && <Loader aria-label="Смотрим, что молчит" size="xs" />}
        </Group>
        <Text size="sm" c="dimmed">
          {isLoading
            ? 'Проверяем, что письма, ответы и фоновые проходы идут как ожидается.'
            : 'Тихо и правильно: письма, ответы и фоновые проходы идут как ожидается. Сторож отличает «ничего не происходит» от «мы перестали слышать» — и молчит только в первом случае.'}
        </Text>
      </Card>
    );
  }

  return (
    <Stack gap="sm">
      {alarms.map((alarm) => (
        <Alert key={alarm.code} color="red" title={alarm.title}>
          {alarm.detail}
        </Alert>
      ))}
    </Stack>
  );
}

interface Step {
  path: string;
  title: string;
  text: string;
  permission?: Permission;
}

/** Порядок — порядок работы: от ключей до цены. */
const STEPS: Step[] = [
  {
    path: '/run',
    title: 'Прогон',
    text: 'Смета до запуска, сбор доменов из выдачи, метрики и судья площадок.',
    permission: 'view',
  },
  {
    path: '/selection',
    title: 'Отбор',
    text: 'Кто прошёл пороги и почему, спорные — на разбор человеку.',
    permission: 'view',
  },
  {
    path: '/donors',
    title: 'Доноры',
    text: 'База площадок с метриками, трафиком по странам и контактами.',
    permission: 'view',
  },
  {
    path: '/letters',
    title: 'Письма',
    text: 'Очередь первых писем и добивок: текст виден и правится до отправки.',
    permission: 'view',
  },
  {
    path: '/threads',
    title: 'Диалоги',
    text: 'Переписка с донорами и распознанные цены на подтверждение.',
    permission: 'view',
  },
  {
    path: '/usage',
    title: 'Расход',
    text: 'На что ушли юниты и деньги и сколько осталось.',
    permission: 'view',
  },
];

export function OverviewPage() {
  const { user, can } = useSession();
  const steps = STEPS.filter((step) => step.permission === undefined || can(step.permission));

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="sm">
          <Title order={3}>Обзор</Title>
          <Text>
            Вошли как <b>{user?.email}</b>.{' '}
            <Anchor component={Link} to="/password" size="sm">
              Сменить пароль
            </Anchor>
          </Text>
          <Group gap="xs">
            <Text size="sm" c="dimmed">
              Доступные действия:
            </Text>
            {user?.permissions.map((permission) => (
              <Badge key={permission} variant="light">
                {permissionTitle(permission)}
              </Badge>
            ))}
          </Group>
        </Stack>
      </Card>

      <Watchdog />

      {steps.length > 0 && (
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md">
          {steps.map((step) => (
            <Card key={step.path} className="glass" p="lg">
              <Anchor component={Link} to={step.path} fw={600}>
                {step.title}
              </Anchor>
              <Text size="sm" c="dimmed" mt={4}>
                {step.text}
              </Text>
            </Card>
          ))}
        </SimpleGrid>
      )}

      <Card className="glass" p="lg">
        <Title order={5} mb="xs">
          Отправка писем
        </Title>
        <Text size="sm" c="dimmed">
          Почта подключается при развёртывании на рабочем сервере. До этого письма собираются,
          читаются и правятся как обычно, но наружу не уходят — экран писем говорит об этом прямо.
        </Text>
      </Card>
    </Stack>
  );
}
