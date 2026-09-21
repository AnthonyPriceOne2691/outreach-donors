/**
 * Обзор. Пока честно говорит, что уже в браузере, а что ещё командой.
 *
 * Пустая главная с надписью «добро пожаловать» хуже, чем список того,
 * чего здесь ещё нет: человек иначе ищет отсутствующий раздел глазами
 * и спрашивает, что сломалось.
 */

import { Alert, Badge, Card, Group, List, Loader, Stack, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { fetchWatchdog } from '../api/settings';
import { permissionTitle } from '../api/labels';
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
  if (isLoading) return <Loader aria-label="Смотрим, что молчит" size="sm" />;

  const alarms = data?.alarms ?? [];
  if (alarms.length === 0) {
    return (
      <Card className="glass" p="lg">
        <Title order={5} mb={4}>
          Сторож тишины
        </Title>
        <Text size="sm" c="dimmed">
          Тихо и правильно: письма, ответы и фоновые проходы идут как ожидается. Сторож отличает
          «ничего не происходит» от «мы перестали слышать» — и молчит только в первом случае.
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

export function OverviewPage() {
  const { user } = useSession();

  return (
    <Stack maw={760} gap="lg">
      <Watchdog />
      <Card className="glassPanel" p="xl">
        <Stack gap="sm">
          <Title order={3}>Обзор</Title>
          <Text>
            Вошли как <b>{user?.email}</b>.
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

      <Card className="glass" p="lg">
        <Title order={5} mb="xs">
          Что сейчас в браузере
        </Title>
        <List size="sm" spacing={4}>
          <List.Item>Вход, смена своего пароля</List.Item>
          <List.Item>Учётки: завести, выдать права, сбросить пароль, отключить — админу</List.Item>
          <List.Item>Прогон: смета до запуска и запуск через очередь</List.Item>
          <List.Item>Доноры: таблица с фильтрами и карточка с адресами</List.Item>
          <List.Item>Диалоги: список переписок и карточка с распознанными ценами</List.Item>
          <List.Item>Пороги: версии с предпросмотром последствий</List.Item>
          <List.Item>Расход: на что ушли юниты и деньги, сколько осталось</List.Item>
          <List.Item>
            Домены рассылки: разгон, дневной расход, включение и выключение — админу
          </List.Item>
        </List>
      </Card>

      <Card className="glass" p="lg">
        <Title order={5} mb="xs">
          Что пока делается командой
        </Title>
        <List size="sm" spacing={4}>
          <List.Item>Сборка пула ключей по пресетам углов</List.Item>
          <List.Item>Поиск контактов ступенями</List.Item>
          <List.Item>Отправка писем: ждёт почтовых доменов и шаблона</List.Item>
        </List>
        <Text size="sm" c="dimmed" mt="sm">
          Экраны прогона и доноров — следующий срез. Порядок и причины — в проектном документе
          веб-слоя.
        </Text>
      </Card>
    </Stack>
  );
}
