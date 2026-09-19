/**
 * Обзор. Пока честно говорит, что уже в браузере, а что ещё командой.
 *
 * Пустая главная с надписью «добро пожаловать» хуже, чем список того,
 * чего здесь ещё нет: человек иначе ищет отсутствующий раздел глазами
 * и спрашивает, что сломалось.
 */

import { Alert, List, Stack, Text, Title } from '@mantine/core';

import { useSession } from '../auth/AuthProvider';

export function OverviewPage() {
  const { user } = useSession();

  return (
    <Stack maw={720}>
      <Title order={3}>Обзор</Title>
      <Text>
        Вошли как <b>{user?.email}</b>. Доступные действия: {user?.permissions.join(', ')}.
      </Text>

      <Alert color="blue" title="Что сейчас в браузере">
        <List size="sm">
          <List.Item>Вход, смена своего пароля</List.Item>
          <List.Item>Учётки: завести, выдать права, сбросить пароль, отключить — админу</List.Item>
        </List>
      </Alert>

      <Alert color="gray" title="Что пока делается командой">
        <List size="sm">
          <List.Item>Сборка пула ключей и прогон по выдаче</List.Item>
          <List.Item>Поиск контактов ступенями</List.Item>
        </List>
        <Text size="sm" mt="xs">
          Экраны прогона и доноров — следующий срез. Порядок и причины — в проектном документе
          веб-слоя.
        </Text>
      </Alert>
    </Stack>
  );
}
